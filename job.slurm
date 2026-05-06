#!/bin/bash
#SBATCH --job-name=pypsa-china-0506.1H.1        # 作业名称
#SBATCH --nodes=1                # 节点数量
#SBATCH --ntasks=1               # 总任务数
#SBATCH --cpus-per-task=32       # 每个任务的CPU核心数
#SBATCH --mem-per-cpu=20G        # 每个CPU核心的内存
#SBATCH --time=15:30:00          # 总运行时间限制
#SBATCH --mail-type=fail         # 作业失败时发送邮件
#SBATCH --mail-user=rl8728@princeton.edu

# 设置日志文件
LOG_FILE="logs/job_0506.1H.1_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== PyPSA-China 0506.1H.1 作业开始 ==="
echo "开始时间: $(date)"
echo "作业ID: $SLURM_JOB_ID"
echo "节点: $SLURM_NODELIST"
echo "日志文件: $LOG_FILE"
echo "版本: 0506.1H.1"
echo

# 加载必要的模块
echo "正在加载模块..."
module purge
module load anaconda3/2024.10
conda activate pypsa-china
module load gurobi/12.0.0

echo "模块加载完成"
echo

# 运行模拟
echo "开始运行0506.1H.1 模拟..."
echo "配置文件: config.yaml"
echo "开始时间: $(date)"
echo

START_TIME=$(date +%s)

# When FORCE_RESTART=1, re-run the full workflow to avoid stale outputs.
FORCE_RESTART="${FORCE_RESTART:-0}"
SNAKEMAKE_EXTRA_ARGS=""
if [ "$FORCE_RESTART" = "1" ]; then
    echo "FORCE_RESTART=1: 使用 --forceall --rerun-incomplete 从头开始运行"
    SNAKEMAKE_EXTRA_ARGS="--forceall --rerun-incomplete"
fi

if snakemake --configfile config.yaml --cores 32 $SNAKEMAKE_EXTRA_ARGS; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo "✓ 0506.1H.1 模拟运行成功！"
    echo "运行时间: $((DURATION / 3600))小时 $((DURATION % 3600 / 60))分钟 $((DURATION % 60))秒"
else
    echo "✗ 0506.1H.1 模拟运行失败！"
    exit 1
fi

echo
echo "=== PyPSA-China 0506.1H.1 作业完成 ==="
echo "完成时间: $(date)"
echo "日志文件: $LOG_FILE"