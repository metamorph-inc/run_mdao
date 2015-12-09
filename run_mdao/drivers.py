from __future__ import print_function
import json
import numpy
import itertools

from openmdao.core.group import Group
from openmdao.core.driver import Driver
from openmdao.util.array_util import evenly_distrib_idxs

from openmdao.util.record_util import create_local_meta
from openmdao.core.mpi_wrap import MPI, debug

import random
from random import shuffle, randint
import numpy as np
from six import moves, itervalues, iteritems

# This failsafe logic can be removed once 'parallel_doe' branch is merged
try:
    from openmdao.core.parallel_doe_group import ParallelDOEGroup
except ImportError:
    def ParallelDOEGroup(num_par_doe):
        return Group()


class PredeterminedRunsDriver(Driver):

    def __init__(self, num_samples=5, *args, **kwargs):
        if type(self) == PredeterminedRunsDriver:
            raise Exception('PredeterminedRunsDriver is an abstract class')
        super(PredeterminedRunsDriver, self).__init__(*args, **kwargs)
        self.num_samples = num_samples

    def _setup(self, root):
        super(PredeterminedRunsDriver, self)._setup(root)
        if MPI and isinstance(root, ParallelDOEGroup):  # pragma: no cover
            comm = root._full_comm
            job_list = None
            if comm.rank == 0:
                debug('Parallel DOE using %d threads' % (root._num_par_doe,))
                run_list = list(self._build_runlist())  # need to run the iterator
                run_sizes, run_offsets = evenly_distrib_idxs(root._num_par_doe, len(run_list))
                job_list = [run_list[o:o+s] for o, s in zip(run_offsets, run_sizes)]
            self.run_list = comm.scatter(job_list, root=0)
            debug('Number of DOE jobs: %s' % (len(self.run_list),))
        else:
            self.run_list = self._build_runlist()

    def run(self, problem):
        log = dict()

        log["get_desvar_metadata"] = self.get_desvar_metadata()

        # Let's iterate and run
        run_list = self._build_runlist()
        # log["runlist"] = list(run_list)

        # Do the runs
        for run in run_list:
            self.run_one(problem, run)

        # Store log
        with open("log.log", "w") as lg:
            json.dump(log, lg, indent=2)

    def run_one(self, problem, run):
        # debug("run: ", run)
        for dv_name, dv_val in run.iteritems():
            self.set_desvar(dv_name, dv_val)

        metadata = create_local_meta(None, 'Driver')
        problem.root.solve_nonlinear(metadata=metadata)
        self.recorders.record_iteration(problem.root, metadata)


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
                value_arrays[name] = list(range(int(value['low']), int(value['high']) + 1))
        # log["arrays"] = value_arrays

        keys = list(value_arrays.keys())
        for combination in itertools.product(*value_arrays.values()):
            yield dict(moves.zip(keys, combination))


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

        for i in moves.xrange(self.num_samples):
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
                buckets[design_var_name] = [numpy.random.uniform(low, high) for low, high in moves.zip(bucket_walls[0:-1], bucket_walls[1:])]
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

        for i in moves.xrange(self.num_samples):
            yield dict(((key, values[i]) for key, values in buckets.iteritems()))


class OptimizedLatinHypercubeDriver(PredeterminedRunsDriver):
    """Design-of-experiments Driver implementing the Morris-Mitchell method for an Optimized Latin Hypercube.
    """

    def __init__(self, num_samples, seed=None, population=20, generations=2, norm_method=1):
        super(OptimizedLatinHypercubeDriver, self).__init__()
        self.qs = [1, 2, 5, 10, 20, 50, 100]  # List of qs to try for Phi_q optimization
        self.num_samples = num_samples
        self.seed = seed
        self.population = population
        self.generations = generations
        self.norm_method = norm_method

    def _build_runlist(self):
        """Build a runlist based on the Latin Hypercube method."""
        design_vars = self.get_desvar_metadata()
        design_vars_names = list(design_vars)
        self.num_design_vars = len(design_vars_names)
        if self.seed is not None:
            random.seed(self.seed)
            np.random.seed(self.seed)

        # Generate an LHC of the proper size
        rand_lhc = self._get_lhc()

        enums = {}
        for design_var_name, metadata in iteritems(design_vars):
            if metadata.get('type', 'double') == 'enum':
                # length is generated such that all items have an equal chance of appearing when num_samples % len(items) != 0
                length = self.num_samples + (-self.num_samples % len(metadata['items']))
                values = list(itertools.islice(itertools.cycle(metadata['items']), length))
                numpy.random.shuffle(values)
                enums[design_var_name] = values
            elif metadata.get('type', 'double') == 'int':
                low, high = int(metadata['low']), int(metadata['high'])
                values = list(moves.xrange(low, high + 1))
                numpy.random.shuffle(values)
                enums[design_var_name] = values

        # Return random values in given buckets
        for i in moves.xrange(self.num_samples):
            def get_random_in_bucket(design_var, bucket):
                metadata = design_vars[design_var]
                if metadata.get('type') == 'enum':
                    return enums[design_var][bucket]
                elif metadata.get('type', 'double') == 'double':
                    low, high = metadata['low'], metadata['high']
                    bucket_size = (high - low) / self.num_samples
                    return numpy.random.uniform(low + bucket_size * bucket, low + bucket_size * (bucket + 1))
                elif metadata.get('type') == 'int':
                    low, high = int(metadata['low']), int(metadata['high'])
                    num_items = int(metadata['high'] - metadata['low'] + 1)

                    if self.num_samples <= num_items:
                        # FIXME do we need to round max up sometimes
                        return numpy.random.randint(low + (num_items * bucket // self.num_samples), low + (num_items * (bucket + 1) // self.num_samples))
                    else:
                        if bucket < self.num_samples - (self.num_samples % num_items):
                            return low + bucket % num_items
                        else:
                            return enums[design_var][bucket % num_items]

            yield {design_var: get_random_in_bucket(design_var, rand_lhc[i, j]) for j, design_var in enumerate(design_vars_names)}

    def _get_lhc(self):
        """Generate an Optimized Latin Hypercube
        """

        rand_lhc = _rand_latin_hypercube(self.num_samples, self.num_design_vars)

        # Optimize our LHC before returning it
        best_lhc = _LHC_Individual(rand_lhc, q=1, p=self.norm_method)
        for q in self.qs:
            lhc_start = _LHC_Individual(rand_lhc, q, self.norm_method)
            lhc_opt = _mmlhs(lhc_start, self.population, self.generations)
            if lhc_opt.mmphi() < best_lhc.mmphi():
                best_lhc = lhc_opt

        return best_lhc._get_doe().astype(int)


class _LHC_Individual(object):
    def __init__(self, doe, q=2, p=1):
        self.q = q
        self.p = p
        self.doe = doe
        self.phi = None  # Morris-Mitchell sampling criterion

    @property
    def shape(self):
        """Size of the LatinHypercube DOE (rows,cols)."""

        return self.doe.shape

    def mmphi(self):
        """Returns the Morris-Mitchell sampling criterion for this Latin hypercube."""

        if self.phi is None:
            n, m = self.doe.shape
            distdict = {}

            # Calculate the norm between each pair of points in the DOE
            arr = self.doe
            for i in range(1, n):
                nrm = np.linalg.norm(arr[i] - arr[:i], ord=self.p, axis=1)
                for j in range(0, i):
                    nrmj = nrm[j]
                    if nrmj in distdict:
                        distdict[nrmj] += 1
                    else:
                        distdict[nrmj] = 1

            distinct_d = np.array(list(distdict))

            # Mutltiplicity array with a count of how many pairs of points have a given distance
            J = np.array(list(itervalues(distdict)))

            self.phi = sum(J * (distinct_d ** (-self.q))) ** (1.0 / self.q)

        return self.phi

    def perturb(self, mutation_count):
        """ Interchanges pairs of randomly chosen elements within randomly chosen
        columns of a DOE a number of times. The result of this operation will also
        be a Latin hypercube.
        """

        new_doe = self.doe.copy()
        n, k = self.doe.shape
        for count in range(mutation_count):
            col = randint(0, k - 1)

            # Choosing two distinct random points
            el1 = randint(0, n - 1)
            el2 = randint(0, n - 1)
            while el1 == el2:
                el2 = randint(0, n - 1)

            new_doe[el1, col] = self.doe[el2, col]
            new_doe[el2, col] = self.doe[el1, col]

        return _LHC_Individual(new_doe, self.q, self.p)

    def __iter__(self):
        return self._get_rows()

    def _get_rows(self):
        for row in self.doe:
            yield row

    def __repr__(self):
        return repr(self.doe)

    def __str__(self):
        return str(self.doe)

    def __getitem__(self, *args):
        return self.doe.__getitem__(*args)

    def _get_doe(self):
        return self.doe


def _rand_latin_hypercube(n, k):
    # Calculates a random Latin hypercube set of n points in k dimensions within [0,n-1]^k hypercube.
    arr = np.zeros((n, k))
    row = list(moves.xrange(0, n))
    for i in moves.xrange(k):
        shuffle(row)
        arr[:, i] = row
    return arr


def _is_latin_hypercube(lh):
    """Returns True if the given array is a Latin hypercube.
    The given array is assumed to be a numpy array.
    """

    n, k = lh.shape
    for j in range(k):
        col = lh[:, j]
        colset = set(col)
        if len(colset) < len(col):
            return False  # something was duplicated
    return True


def _mmlhs(x_start, population, generations):
    """Evolutionary search for most space filling Latin-Hypercube.
    Returns a new LatinHypercube instance with an optimized set of points.
    """

    x_best = x_start
    phi_best = x_start.mmphi()
    n = x_start.shape[1]

    level_off = np.floor(0.85 * generations)
    for it in range(generations):
        if it < level_off and level_off > 1.:
            mutations = int(round(1 + (0.5 * n - 1) * (level_off - it) / (level_off - 1)))
        else:
            mutations = 1

        x_improved = x_best
        phi_improved = phi_best

        for offspring in range(population):
            x_try = x_best.perturb(mutations)
            phi_try = x_try.mmphi()

            if phi_try < phi_improved:
                x_improved = x_try
                phi_improved = phi_try

        if phi_improved < phi_best:
            phi_best = phi_improved
            x_best = x_improved

    return x_best
