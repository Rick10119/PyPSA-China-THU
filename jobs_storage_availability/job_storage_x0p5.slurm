#!/bin/bash
#SBATCH --job-name=storage-x0p5
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem-per-cpu=20G
#SBATCH --time=71:59:00
#SBATCH --mail-type=fail
#SBATCH --mail-user=rl8728@princeton.edu

mkdir -p logs
LOG_FILE="logs/storage_x0p5_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Storage availability sensitivity: x0p5 ==="
echo "Start time: $(date)"
echo "Job ID: $SLURM_JOB_ID"
echo "Config: configs/storage_availability_sensitivity/config_storage_x0p5.yaml"

module purge
module load anaconda3/2024.10
conda activate pypsa-china
module load gurobi/12.0.0

FORCE_RESTART="${FORCE_RESTART:-0}"
SNAKEMAKE_EXTRA_ARGS=""
if [ "$FORCE_RESTART" = "1" ]; then
    SNAKEMAKE_EXTRA_ARGS="--forceall --rerun-incomplete"
fi

snakemake --configfile "configs/storage_availability_sensitivity/config_storage_x0p5.yaml" --cores 32 $SNAKEMAKE_EXTRA_ARGS

mkdir -p "results/version-0621.1H.3-storage-x0p5"
if [ "$(cd "$(dirname "results/version-0605.1H.2/solar_value_dataset.xlsx")" && pwd)/$(basename "results/version-0605.1H.2/solar_value_dataset.xlsx")" != "$(cd "results/version-0621.1H.3-storage-x0p5" && pwd)/solar_value_dataset.xlsx" ]; then
    cp "results/version-0605.1H.2/solar_value_dataset.xlsx" "results/version-0621.1H.3-storage-x0p5/solar_value_dataset.xlsx"
fi

python scripts/fill_solar_value_dataset_2025.py --config "configs/storage_availability_sensitivity/config_storage_x0p5.yaml" --planning-marginal --skip-plot

echo "Finished: $(date)"
echo "Log file: $LOG_FILE"
