"""Runs a simple regression test."""

import run_mdao
import glob
import os
import os.path
import shutil
import subprocess

_this_dir = os.path.dirname(os.path.abspath(__file__))

for input_filename in glob.glob(_this_dir + '/mdao_config*json'):
    os.chdir(_this_dir)
    run_mdao.run(input_filename)
    output_filename = input_filename + '.output.csv'
    shutil.copyfile('output.csv', output_filename)
    changed = subprocess.check_output('git diff --name-only'.split() + [output_filename])
    if len(changed) > 0:
        raise Exception(changed)
