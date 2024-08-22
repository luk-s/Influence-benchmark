#!/bin/bash

# Get the current username
export CURRENT_USER=$(whoami)
export PROJ_DIR="/nas/ucb/$CURRENT_USER/Influence-benchmark"

###############################################################
# PARAMETERS

# Python file to run (should be in `experiments` directory)
export FILE_TO_RUN="run_experiment.py"

# By default, have the slurm job name be the same as the Python file
export JOB_NAME=$FILE_TO_RUN

# SLURM job parameters
export SLURM_OUTPUT="slurm/%j.out"
export SLURM_CPUS_PER_TASK=10
export SLURM_MEM="100gb"
export SLURM_GPUS="A6000:1"
export SLURM_TIME="00:03:00"
export SLURM_NODES=1
export SLURM_NTASKS_PER_NODE=1

###############################################################

bash $PROJ_DIR/influence_benchmark/experiments/slurm/autocopy_and_sbtach.sh