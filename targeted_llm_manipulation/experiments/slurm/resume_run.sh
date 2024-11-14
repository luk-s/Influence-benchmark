#!/bin/bash

###############################################################
# PARAMETERS

export CONFIG_NAME="therapy"
export TIMESTAMP="10_14_120552"

# SLURM job parameters
export SLURM_CPUS_PER_TASK=8
export SLURM_MEM="150gb"
export SLURM_GPUS="4"
export GPU_TYPE="either" # A100 (faster generation) or A6000 (often more available), or "either"
export SLURM_TIME="12:00:00"
export SLURM_QOS="default" # can set to high if this is blocking your progress and you only need one/two jobs to run

###############################################################



# Get the directory of the current script
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"


FILE_TO_RUN="run_experiment.py"

# Check if /nas/ directory exists to determine if we're on the CHAI cluster
if [ -d "/nas" ]; then
<<<<<<< HEAD:targeted_llm_manipulation/experiments/slurm/resume_run.sh
    PROJ_DIR="/nas/ucb/$(whoami)/Targeted-Manipulation-and-Deception-in-LLMs"
=======
    PROJ_DIR="/nas/ucb/$(whoami)/code/Influence-benchmark"
>>>>>>> Updated absolute paths.:influence_benchmark/experiments/slurm/resume_run.sh

    if [ "$GPU_TYPE" == "A100" ]; then
        NODE_LIST="cirl.ist.berkeley.edu,rlhf.ist.berkeley.edu,airl.ist.berkeley.edu,sac.ist.berkeley.edu"
    elif [ "$GPU_TYPE" == "A6000" ]; then
        NODE_LIST="ddpg.ist.berkeley.edu,dqn.ist.berkeley.edu,gail.ist.berkeley.edu,gan.ist.berkeley.edu"
    elif [ "$GPU_TYPE" == "either" ]; then
        NODE_LIST="cirl.ist.berkeley.edu,rlhf.ist.berkeley.edu,airl.ist.berkeley.edu,sac.ist.berkeley.edu,ddpg.ist.berkeley.edu,dqn.ist.berkeley.edu,gail.ist.berkeley.edu,gan.ist.berkeley.edu"
    else
        echo "Invalid GPU type: $GPU_TYPE"
        exit 1
    fi

    NODE_PARAM="--nodelist=$NODE_LIST"
    MEM_PARAM="#SBATCH --mem=$SLURM_MEM"
    QOS="#SBATCH --qos=$SLURM_QOS"
    # If $SLURM_QOS is "scavenger", we need to specify the partition
    if [ "$SLURM_QOS" == "scavenger" ]; then
        QOS="$QOS --partition scavenger"
    fi
else
    # If we're on CAIS, specifying memory doesn't work, and the nodes are different so they can be ignored.
    # Also, we need to use the "single" partition or things error.
    PROJ_DIR="$HOME/Targeted-Manipulation-and-Deception-in-LLMs"
    NODE_PARAM="--partition=single"
    MEM_PARAM=""
    QOS=""
fi

# Generate timestamp
JOB_NAME="${CONFIG_NAME}_${TIMESTAMP}"
TEMP_DIR="$PROJ_DIR/tmp/tmp_$TIMESTAMP"

# Fixed SLURM params
export SLURM_NODES=1
export SLURM_NTASKS_PER_NODE=1
export SLURM_OUTPUT="$PROJ_DIR/slurm_logging/$JOB_NAME-%j.out"

# Check if we're already in the correct Conda environment
if [[ "$CONDA_DEFAULT_ENV" != "influence" ]]; then
    echo "Error: Not in the 'influence' Conda environment. Please activate it before running this script."
    exit 1
fi

echo "Using Conda environment: $CONDA_DEFAULT_ENV"
echo "Python path: $(which python)"

# Define the original project directory
ORIGINAL_DIR="$PROJ_DIR/targeted_llm_manipulation"

# Create a unique temporary directory and copy the project to it
echo "Creating temporary directory: $TEMP_DIR"
mkdir -p $TEMP_DIR
cp -r $ORIGINAL_DIR $TEMP_DIR

# Modify the import statements in the tmp copy
cd $TEMP_DIR/targeted_llm_manipulation
python utils/prep_for_slurm.py . $FILE_TO_RUN



cat << EOF > $JOB_NAME
#!/bin/bash
#SBATCH --output=$SLURM_OUTPUT
#SBATCH --cpus-per-task=$SLURM_CPUS_PER_TASK
#SBATCH --gpus=$SLURM_GPUS
#SBATCH --time=$SLURM_TIME
#SBATCH --nodes=$SLURM_NODES
#SBATCH --ntasks-per-node=$SLURM_NTASKS_PER_NODE
#SBATCH $NODE_PARAM
$MEM_PARAM
$QOS

# module load anaconda3
export NCCL_P2P_LEVEL=NVL
conda activate influence
echo "Conda environment: \$CONDA_DEFAULT_ENV"

# Get the file to run and the temporary directory from command-line arguments
FILE_TO_RUN=\$1
TEMP_DIR=\$2/targeted_llm_manipulation

# Change to the temporary directory
cd \$TEMP_DIR

# Run the Python script
python experiments/\$FILE_TO_RUN --config \$CONFIG_NAME.yaml --all-gpus --timestamp \$TIMESTAMP

# Optional: Clean up the temporary directory after the job finishes
# Uncomment the following line if you want to automatically delete the temporary directory
# rm -rf \$TEMP_DIR
EOF

# Run the SLURM job
echo Command to run: "python experiments/$FILE_TO_RUN --config $CONFIG_NAME.yaml"
sbatch $JOB_NAME $FILE_TO_RUN $TEMP_DIR
