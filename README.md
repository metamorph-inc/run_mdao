`pip install --upgrade https://github.com/metamorph-inc/run_mdao/tarball/master`

### Parallel Execution


Repos:  
https://github.com/metamorph-inc/OpenMDAO parallel_doe  
https://github.com/metamorph-inc/run_mdao master  
https://github.com/metamorph-inc/testbenchexecutor master  
https://bitbucket.org/metamorphsoftwareinc/openmdao-couchdb-recorder master  


##### On all nodes:

Use the same file paths on every machine. Optional: put a Python virtualenv and PETSc on NFS.

1. You need to have MPICH installed:

    ```
apt-get install -y libmpich-dev mpich2 libmpich2-dev mpich2python
    ```

1. Also need PETSc:

    ```
apt-get install -y build-essential libblas-dev liblapack-dev moreutils wget
wget http://ftp.mcs.anl.gov/pub/petsc/release-snapshots/petsc-3.6.2.tar.gz
tar xzvf petsc-3.6.2.tar.gz
cd petsc-3.6.2
./configure
make all
export PETSC_DIR=$(pwd)
echo export PETSC_DIR="$(pwd)" | cat - ~/.bashrc | sponge ~/.bashrc
    ```

1. Get the OpenMDAO and run_mdao repos (using the branches above) and set them up.

1. Add all hosts to /etc/hosts (or otherwise enable hostname resolution)

1. Install dependencies

    ```
Optional: apt-get install libatlas-dev
pip install mpi4py petsc4py
pip install testbenchexecutor
Optional: pip install openmdao-couchdb-recorder
    ```

1. Install XFOIL or whatever is needed

1. Run sshd and enable private key authentication from the root

    ```
apt-get install -y openssh-server
/etc/init.d/ssh start
cat >> ~/.ssh/authorized_keys
    ```

##### On root machine:

1. Create a hosts file containing IPs and number of cores, e.g.:

    ```
10.240.89.115:8
10.240.0.2:1
    ```

2. In the 'output' (generated) folder execute:

    ```
PYTHONPATH=...run_mdao/.. mpirun -f hosts -n {total-number-of-processes} python -m ...run_mdao/__main__.py
    ```
