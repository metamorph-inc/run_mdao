import sys
import csv
from collections import OrderedDict

import numpy

from openmdao.recorders.base_recorder import BaseRecorder


class CsvRecorder(BaseRecorder):

    def __init__(self, out=sys.stdout):
        super(CsvRecorder, self).__init__()

        self._wrote_header = False
        self._parallel = False

        if out != sys.stdout:
            self.out = out
        self.writer = csv.writer(out)

    def startup(self, group):
        super(CsvRecorder, self).startup(group)

    def record_iteration(self, params, unknowns, resids, metadata):
        if self._wrote_header is False:
            self.writer.writerow([param for param in params] + [unknown for unknown in unknowns])
            self._wrote_header = True

        def munge(val):
            if isinstance(val, numpy.ndarray):
                return ",".join(map(str, val))
            return str(val)
        self.writer.writerow([munge(value['val']) for value in params.values()] + [munge(value['val']) for value in unknowns.values()])

        if self.out:
            self.out.flush()

    def record_metadata(self, group):
        pass
        # TODO: what to do here?
        # self.writer.writerow([param.name for param in group.params] + [unknown.name for unknowns in group.unknowns])


class MappingCsvRecorder(BaseRecorder):

    def __init__(self, params_map, unknowns_map, out=sys.stdout):
        super(MappingCsvRecorder, self).__init__()

        self._wrote_header = False
        self._parallel = False

        self.params_map = OrderedDict(((k, v) for k, v in sorted(params_map.iteritems())))
        self.unknowns_map = OrderedDict(((k, v) for k, v in sorted(unknowns_map.iteritems())))

        if out != sys.stdout:
            self.out = out
        self.writer = csv.writer(out)

    def startup(self, group):
        super(MappingCsvRecorder, self).startup(group)

    def record_iteration(self, params, unknowns, resids, metadata):
        if self._wrote_header is False:
            self.writer.writerow(list(self.params_map.values()) + list(self.unknowns_map.values()))
            self._wrote_header = True

        def munge(val):
            if isinstance(val, numpy.ndarray):
                return ",".join(map(str, val))
            return str(val)

        def do_mapping(map_, values):
            return [munge(values[key]) for key in map_]
        # import pdb; pdb.set_trace()
        self.writer.writerow(do_mapping(self.params_map, params) + do_mapping(self.unknowns_map, unknowns))

        if self.out:
            self.out.flush()

    def record_metadata(self, group):
        pass
        # TODO: what to do here?
        # self.writer.writerow([param.name for param in group.params] + [unknown.name for unknowns in group.unknowns])
