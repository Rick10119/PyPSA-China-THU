#!/bin/bash
#SBATCH --job-name=solar-capacity-export
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=01:30:00
#SBATCH --mail-type=fail
#SBATCH --mail-type=end
#SBATCH --mail-type=start
#SBATCH --mail-user=rl8728@princeton.edu

# 设置日志文件
LOG_FILE="logs/job_solar_capacity_export_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== Solar capacity export job start ==="
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

VERSION="${VERSION:-}"
CONFIG_PATH="${CONFIG_PATH:-config.yaml}"
OUTPUT_PATH="${OUTPUT_PATH:-}"
STRICT="${STRICT:-0}"
STRICT_ARG=""
if [ "$STRICT" = "1" ]; then
    STRICT_ARG="--strict"
fi

echo "运行脚本: scripts/export_national_solar_capacity_from_planning_nc.py"
echo "CONFIG_PATH=$CONFIG_PATH"
if [ -n "$VERSION" ]; then
    echo "VERSION=$VERSION (env override)"
else
    echo "VERSION=<from config>"
fi
echo "OUTPUT_PATH=${OUTPUT_PATH:-<default>}"
echo "STRICT=$STRICT"
echo

CMD="python scripts/export_national_solar_capacity_from_planning_nc.py --config \"$CONFIG_PATH\" $STRICT_ARG"
if [ -n "$VERSION" ]; then
    CMD="$CMD --version \"$VERSION\""
fi
if [ -n "$OUTPUT_PATH" ]; then
    CMD="$CMD --output \"$OUTPUT_PATH\""
fi

echo "执行命令: $CMD"
eval "$CMD"
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo "✓ Solar capacity export 成功"
    echo "运行时间: $((DURATION / 3600))小时 $((DURATION % 3600 / 60))分钟 $((DURATION % 60))秒"
else
    echo "✗ Solar capacity export 失败"
    exit $EXIT_CODE
fi

echo
echo "=== Solar capacity export job finished ==="
echo "完成时间: $(date)"
echo "日志文件: $LOG_FILE"