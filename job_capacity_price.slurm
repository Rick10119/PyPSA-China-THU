#!/bin/bash
#SBATCH --job-name=fill-solar-value-dataset
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=00:30:00
#SBATCH --mail-type=fail
#SBATCH --mail-type=end
#SBATCH --mail-type=begin
#SBATCH --mail-user=rl8728@princeton.edu

# 设置日志文件
LOG_FILE="logs/job_fill_solar_value_dataset_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Fill solar value dataset job start ==="
echo "开始时间: $(date)"
echo "作业ID: $SLURM_JOB_ID"
echo "节点: $SLURM_NODELIST"
echo "日志文件: $LOG_FILE"
echo

# 加载环境
echo "正在加载环境..."
module purge
module load anaconda3/2024.10
conda activate pypsa
echo "环境加载完成"
echo

START_TIME=$(date +%s)

echo "运行脚本: scripts/fill_solar_value_dataset_2025.py"
echo

CMD="python scripts/fill_solar_value_dataset_2025.py  --mapped-csv"

echo "执行命令: $CMD"
eval "$CMD"
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo "✓ Fill solar value dataset 成功"
    echo "运行时间: $((DURATION / 3600))小时 $((DURATION % 3600 / 60))分钟 $((DURATION % 60))秒"
else
    echo "✗ Fill solar value dataset 失败"
    exit $EXIT_CODE
fi

echo
echo "=== Fill solar value dataset job finished ==="
echo "完成时间: $(date)"
echo "日志文件: $LOG_FILE"