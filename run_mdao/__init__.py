from __future__ import print_function
import sys
import os.path
import contextlib
import json
import subprocess
import collections
import importlib
import time

import numpy
import itertools

import six.moves

from run_mdao.csv_recorder import MappingCsvRecorder
from run_mdao.enum_mapper import EnumMapper

# from openmdao.api import IndepVarComp, Component, Problem, Group
from openmdao.core.problem import Problem
from openmdao.core.component import Component
from openmdao.core.group import Group
from openmdao.components.indep_var_comp import IndepVarComp
from openmdao.api import ScipyOptimizer
from openmdao.core.driver import Driver


def CouchDBRecorder(*args, **kwargs):
    '''Lazy load CouchDBRecorder'''
    from couchdb_recorder.couchdb_recorder import CouchDBRecorder
    return CouchDBRecorder(*args, **kwargs)


from openmdao.util.record_util import create_local_meta, update_local_meta

# cache the output of a TestBenchComponent if the computation exceeds this many seconds. Otherwise, save memory by throwing it out
CACHE_THRESHOLD_SECONDS = 5


def _memoize_solve(fn):
    from run_mdao.array_hashable import array_hashable
    memo = {}

    def solve_nonlinear(tb_params, unknowns, resids):
        def unwrap_param(param):
            if param.get('pass_by_obj', False):
                return unwrap_val(param['val'].val)
            return unwrap_val(param['val'])

        def unwrap_val(val):
            if isinstance(val, numpy.ndarray):
                return array_hashable(val)
            return val
        hashable = tuple((unwrap_param(param) for param in tb_params.values()))
        memoized_unknowns = memo.get(hashable, None)
        if memoized_unknowns:
            # print('cache hit')
            for name, value in memoized_unknowns.iteritems():
                unknowns[name] = value
            return
        start = time.time()
        fn(tb_params, unknowns, resids=resids)
        if time.time() - start >= CACHE_THRESHOLD_SECONDS:
            memo[hashable] = {key: (value['val'].val if value.get('pass_by_obj', False) else value['val']) for key, value in unknowns.iteritems()}

    return solve_nonlinear


def _get_param_name(param_name):
    """OpenMDAO won't let us have a parameter and output of the same name..."""
    return 'param_{}'.format(param_name)


class PredeterminedRunsDriver(Driver):
    def __init__(self, num_samples=5, *args, **kwargs):
        if type(self) == PredeterminedRunsDriver:
            raise Exception('PredeterminedRunsDriver is an abstract class')
        super(PredeterminedRunsDriver, self).__init__(*args, **kwargs)
        self.num_samples = num_samples

    def run(self, problem):
        log = dict()

        log["get_desvar_metadata"] = self.get_desvar_metadata()

        # Let's iterate and run
        run_list = self._build_runlist()
        # log["runlist"] = list(run_list)

        # Do the runs
        for run in run_list:
            print("run: ", run)
            for dv_name, dv_val in run.iteritems():
                self.set_desvar(dv_name, dv_val)

            metadata = create_local_meta(None, 'Driver')
            problem.root.solve_nonlinear(metadata=metadata)
            self.recorders.record_iteration(problem.root, metadata)

        # Store log
        with open("log.log", "w") as lg:
            json.dump(log, lg, indent=2)


class FullFactorialDriver(PredeterminedRunsDriver):
    def __init__(self, *args, **kwargs):
        super(FullFactorialDriver, self).__init__(*args, **kwargs)

    def _build_runlist(self):
        # Set up Uniform distribution arrays
        value_arrays = dict()
        for name, value in self.get_desvar_metadata().iteritems():
            if value.get('type', 'double') == 'double':
                low = value['low']
                high = value['high']
                value_arrays[name] = numpy.linspace(low, high, num=self.num_samples).tolist()
            elif value.get('type') == 'enum':
                value_arrays[name] = list(value['items'])
            elif value.get('type') == 'int':
                value_arrays[name] = list(range(value['low'], value['high'] + 1))
        # log["arrays"] = value_arrays

        keys = list(value_arrays.keys())
        for combination in itertools.product(*value_arrays.values()):
            yield dict(six.moves.zip(keys, combination))


class UniformDriver(PredeterminedRunsDriver):
    def __init__(self, *args, **kwargs):
        super(UniformDriver, self).__init__(*args, **kwargs)

    def _build_runlist(self):
        def sample_var(metadata):
            if metadata.get('type', 'double') == 'double':
                return numpy.random.uniform(metadata['low'], metadata['high'])
            elif metadata.get('type') == 'enum':
                return numpy.random.choice(metadata['items'])
            elif metadata.get('type') == 'int':
                return numpy.random.randint(metadata['low'], metadata['high'] + 1)

        for i in six.moves.xrange(self.num_samples):
            yield dict(((key, sample_var(metadata)) for key, metadata in self.get_desvar_metadata().iteritems()))


class LatinHypercubeDriver(PredeterminedRunsDriver):
    def __init__(self, *args, **kwargs):
        super(LatinHypercubeDriver, self).__init__(*args, **kwargs)

    def _build_runlist(self):
        design_vars = self.get_desvar_metadata()
        design_vars_names = list(design_vars)
        buckets = dict()
        for design_var_name in design_vars_names:
            metadata = design_vars[design_var_name]
            if metadata.get('type', 'double') == 'double':
                bucket_walls = numpy.linspace(metadata['low'], metadata['high'], num=self.num_samples + 1)
                buckets[design_var_name] = [numpy.random.uniform(low, high) for low, high in six.moves.zip(bucket_walls[0:-1], bucket_walls[1:])]
            elif metadata.get('type') == 'enum':
                # length is generated such that all items have an equal chance of appearing when num_samples % len(items) != 0
                length = self.num_samples + (-self.num_samples % len(metadata['items']))
                buckets[design_var_name] = list(itertools.islice(itertools.cycle(metadata['items']), length))
            elif metadata.get('type') == 'int':
                # FIXME: should do buckets instead
                num_items = int(metadata['high'] - metadata['low'] + 1)
                length = self.num_samples + (-self.num_samples % num_items)
                buckets[design_var_name] = list(itertools.islice(itertools.cycle(range(int(metadata['low']), int(metadata['high'] + 1))), length))

            numpy.random.shuffle(buckets[design_var_name])

        for i in six.moves.xrange(self.num_samples):
            yield dict(((key, values[i]) for key, values in buckets.iteritems()))


class TestBenchComponent(Component):
    def __init__(self, name, mdao_config, root):
        super(TestBenchComponent, self).__init__()
        self.name = name
        self.mdao_config = mdao_config
        self.directory = mdao_config['components'][name]['details']['directory']
        self.original_testbench_manifest = self._read_testbench_manifest()

        self.fd_options['force_fd'] = True

        for param_name, param in mdao_config['components'][name].get('parameters', {}).iteritems():
            pass_by_obj = source_is_not_driver = param.get('source', [''])[0] not in mdao_config['drivers']
            val = 0.0
            if source_is_not_driver and 'source' in param:
                source_component = {c.name: c for c in root.components()}[param['source'][0]]
                val = source_component._unknowns_dict[param['source'][-1]]['val']

            self.add_param(_get_param_name(param_name), val=val, pass_by_obj=pass_by_obj)

        for metric_name, _ in mdao_config['components'][name].get('unknowns', {}).iteritems():
            self.add_output(metric_name, val=0.0, pass_by_obj=True)

    def _read_testbench_manifest(self):
        with open(os.path.join(self.directory, 'testbench_manifest.json'), 'rb') as testbench_manifest_json:
            return json.loads(testbench_manifest_json.read())

    def _write_testbench_manifest(self, testbench_manifest):
        # print(repr(testbench_manifest))
        output = json.dumps(testbench_manifest, indent=4)
        with open(os.path.join(self.directory, 'testbench_manifest.json'), 'wb') as testbench_manifest_json:
            testbench_manifest_json.write(output)

    def _run_testbench(self):
        return subprocess.call([sys.executable, '-m', 'testbenchexecutor', 'testbench_manifest.json'], cwd=self.directory)

    def solve_nonlinear(self, params, unknowns, resids):
        # TODO null out metrics and check they are set after execution
        for param_name, param_value in params.iteritems():
            param_name = param_name[len(_get_param_name('')):]
            for manifest_param in self.original_testbench_manifest['Parameters']:
                if manifest_param['Name'] == param_name:
                    val = param_value['val']
                    if param_value.get('pass_by_obj', True):
                        val = val.val
                    if isinstance(val, numpy.ndarray):
                        # manifest_param['Value'] = list((numpy.asscalar(v) for v in param_value['val']))
                        if len(val) == 1:
                            manifest_param['Value'] = val[0]  # FIXME seems we always get an ndarray from the DOE. Why?
                        else:
                            manifest_param['Value'] = val.tolist()
                    else:
                        # manifest_param['Value'] = numpy.asscalar(param_value['val'].val)
                        manifest_param['Value'] = val
                    break
            else:
                raise Exception('Could not find parameter "{}" in {}/testbench_manifest.json'.format(param_name, self.directory))

        self._write_testbench_manifest(self.original_testbench_manifest)

        ret_code = self._run_testbench()

        testbench_manifest = self._read_testbench_manifest()

        for metric_name in self.mdao_config['components'][self.name].get('unknowns', {}):
            if ret_code != 0:
                unknowns[metric_name] = None
            for testbench_metric in testbench_manifest['Metrics']:
                if metric_name == testbench_metric['Name']:
                    value = testbench_metric['Value']
                    if isinstance(value, list):
                        unknowns[metric_name] = numpy.array(value)
                    else:
                        unknowns[metric_name] = value
                    break
            else:
                raise ValueError('Could not find metric "{}" in {}/testbench_manifest.json'.format(metric_name, self.directory))

    def jacobian(self, params, unknowns, resids):
        raise Exception('unsupported')


def run(filename):
    with open(filename, 'rb') as mdao_config_json:
        mdao_config = json.loads(mdao_config_json.read())
    # TODO: can we support more than one driver
    driver = next(iter(mdao_config['drivers'].values()))

    top = Problem()
    root = top.root = Group()
    recorder = None
    driver_params = {}
    eval(compile(driver['details']['Code'], '<driver Code>', 'exec'), globals(), driver_params)

    def get_desvar_path(designVariable):
        return 'designVariable.{}'.format(designVariable)

    if driver['type'] == 'optimizer':
        top.driver = ScipyOptimizer()
        top.driver.options['optimizer'] = 'SLSQP'  # FIXME read from driver['details']['OptimizationFunction']
    elif driver['type'] == 'parameterStudy':
        drivers = {
            "Uniform": UniformDriver,
            "Full Factorial": FullFactorialDriver,
            "Latin Hypercube": LatinHypercubeDriver,  # FIXME this is not in CyPhyML.xme
            "Opt Latin Hypercube": LatinHypercubeDriver,  # FIXME workaround for no "Latin Hypercube" in CyPhyML.xme
        }
        driver_type = drivers.get(driver['details']['DOEType'])
        if driver_type is None:
            raise Exception('DOEType "{}" is unsupported'.format(driver['details']['DOEType']))
        top.driver = driver_type(**driver_params)

        # top.driver.options[''] = ...
    else:
        raise Exception('Driver "{}" is unsupported'.format(driver['type']))

    def add_recorders():
        recorders = []
        design_var_map = {get_desvar_path(designVariable): designVariable for designVariable in driver['designVariables']}
        objective_map = {'{}.{}'.format(objective['source'][0], objective['source'][1]): objective_name for objective_name, objective in driver['objectives'].iteritems()}
        unknowns_map = design_var_map
        unknowns_map.update(objective_map)
        for recorder in mdao_config.get('recorders', [{'type': 'DriverCsvRecorder', 'filename': 'output.csv'}]):
            if recorder['type'] == 'DriverCsvRecorder':
                recorder = MappingCsvRecorder({}, unknowns_map, open(recorder['filename'], 'wb'))
            elif recorder['type'] == 'CouchDBRecorder':
                recorder = CouchDBRecorder(recorder.get('url', 'http://localhost:5984/'), recorder['run_id'])
                recorder.options['record_params'] = True
                recorder.options['record_unknowns'] = True
                recorder.options['record_resids'] = False
                recorder.options['includes'] = unknowns_map.keys()
            top.driver.add_recorder(recorder)
        return recorders

    recorders = add_recorders()

    try:
        driver_vars = []
        for var_name, var in driver['designVariables'].iteritems():
            if var.get('type', 'double') == 'double':
                driver_vars.append((var_name, 0.0))
            elif var['type'] == 'enum':
                driver_vars.append((var_name, u'', {"pass_by_obj": True}))
            elif var['type'] == 'int':
                driver_vars.append((var_name, 0.0))
            else:
                raise ValueError('Unimplemented designVariable type "{}"'.format(var['type']))

        root.add(get_desvar_path('').split('.')[0], IndepVarComp(driver_vars))
        for var_name, var in driver['designVariables'].iteritems():
            if var.get('type', 'double') == 'double':
                top.driver.add_desvar(get_desvar_path(var_name), low=var.get('RangeMin'), high=var.get('RangeMax'))
            elif var['type'] == 'enum':
                driver_vars.append((var_name, u'', {"pass_by_obj": True}))
                formatted_name = get_desvar_path(var_name)
                top.driver.add_desvar(formatted_name)
                top.driver._desvars[formatted_name]['type'] = var['type']
                top.driver._desvars[formatted_name]['items'] = var['items']
            elif var['type'] == 'int':
                driver_vars.append((var_name, 0.0))
                formatted_name = get_desvar_path(var_name)
                top.driver.add_desvar(formatted_name, low=var.get('RangeMin'), high=var.get('RangeMax'))
                top.driver._desvars[formatted_name]['type'] = var['type']
            else:
                raise ValueError('Unimplemented designVariable type "{}"'.format(var['type']))

        def get_sorted_components():
            """Applies Tarjan's algorithm to the Components."""
            visited = {}
            tbs_sorted = []

            def get_ordinal(name):
                ordinal = visited.get(name, -1)
                if ordinal is None:
                    raise ValueError('Loop involving component "{}"'.format(name))
                if ordinal != -1:
                    return ordinal
                component = mdao_config['components'][name]
                visited[name] = None
                ordinal = 0
                for source in (param.get('source') for param in component.get('parameters', {}).values()):
                    if not source:
                        continue
                    if source[0] in mdao_config['drivers']:
                        continue
                    ordinal = max(ordinal, get_ordinal(source[0]) + 1)
                visited[name] = ordinal
                tbs_sorted.append(name)
                return ordinal

            for component_name in mdao_config['components']:
                get_ordinal(component_name)
            return tbs_sorted

        tbs_sorted = get_sorted_components()
        for component_name in tbs_sorted:
            component = mdao_config['components'][component_name]
            component_type = component.get('type', 'TestBenchComponent')
            if component_type == 'IndepVarComp':
                vars = ((name, metric['value'], {'pass_by_obj': True}) for name, metric in component['unknowns'].iteritems())
                root.add(component_name, IndepVarComp(vars))
            elif component_type == 'TestBenchComponent':
                tb = TestBenchComponent(component_name, mdao_config, root)
                tb.solve_nonlinear = _memoize_solve(tb.solve_nonlinear)

                root.add(component_name, tb)
            elif component_type == 'EnumMap':
                root.add(component_name, EnumMapper(component['details']['config'], param_name=_get_param_name('input')))
            else:
                if '.' in component_type:
                    component_instance = importlib.import_module('.'.join(component_type.split('.')[:-1]))[component_type.split('.')[-1]](**component['details'])
                else:
                    component_instance = locals()[component_type](**component['details'])
                root.add(component_name, component_instance)

        for component_name, component in mdao_config['components'].iteritems():
            for parameter_name, parameter in component.get('parameters', {}).iteritems():
                if parameter.get('source'):
                    source = parameter['source']
                    if source[0] in mdao_config['drivers']:
                        # print('driver{}.{}'.format(source[1], source[1]))
                        root.connect(get_desvar_path(source[1]), '{}.{}'.format(component_name, _get_param_name(parameter_name)))
                    else:
                        root.connect('{}.{}'.format(source[0], source[1]), '{}.{}'.format(component_name, _get_param_name(parameter_name)))
                else:
                    pass  # TODO warn or fail?

        if driver['type'] == 'optimizer':
            for objective in driver['objectives'].itervalues():
                top.driver.add_objective(str('.'.join(objective['source'])))

        top.setup()
        top.run()
    finally:
        for recorder in recorders:
            recorder.close()
