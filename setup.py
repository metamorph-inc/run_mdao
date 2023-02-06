r"""
Build with:
setup.py bdist_wheel
or install in editable mode with
Scripts\pip install -e path\to\run_mdao
"""
from __future__ import absolute_import
from setuptools import setup

setup(
    name='run_mdao',
    version='0.5.0rc2',
    author='MetaMorph Software, Inc',
    author_email='ksmyth@metamorphsoftware.com',
    description='Runs a mdao_config.json with OpenMDAO',
    packages=['run_mdao', 'run_mdao.python_component', 'run_mdao.python_component.get_params_and_unknowns'],
    install_requires=['mms-openmdao>=1.7.5rc1,<2', 'testbenchexecutor'],
    package_dir={'run_mdao': 'run_mdao'},
    entry_points={
        "console_scripts": [
            "run_mdao = run_mdao.__main__:main",
        ]
    },
    project_urls={
        'Source': 'https://github.com/metamorph-inc/run_mdao',
    },
)
