'''
Build with:
setup.py bdist_wheel
or install in editable mode with
Scripts\pip install -e path\to\run_mdao
'''
from setuptools import setup

setup(
    name='run_mdao',
    version='0.1.0',
    author='MetaMorph Software, Inc',
    author_email='ksmyth@metamorphsoftware.com',
    description='Runs a mdao_config.json with OpenMDAO',
    packages=['run_mdao'],
    package_dir={'run_mdao': 'run_mdao'},
    entry_points = {
        "console_scripts": [
            "run_mdao = run_mdao.__main__:main",
        ]
    }
)
