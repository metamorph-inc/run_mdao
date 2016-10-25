import sys
import json
import run_mdao.python_component
import numpy
from openmdao.api import FileRef


def main(python_filename):

    def default(obj):
        if isinstance(obj, FileRef):
            return repr(obj)
        if isinstance(obj, numpy.ndarray):
            return repr(obj)
        if isinstance(obj, type):
            return obj.__name__
        raise TypeError(repr(obj) + " is not JSON serializable")

    c = run_mdao.python_component.PythonComponent(python_filename)
    return json.dumps({'params': c._init_params_dict, 'unknowns': c._init_unknowns_dict}, default=default)

if __name__ == '__main__':
    print(main(sys.argv[1]))
