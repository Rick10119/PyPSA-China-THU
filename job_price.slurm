#!/bin/bash
#SBATCH --job-name=pypsa-export-prices
#SBATCH --nodes=1                # 节点数量
#SBATCH --ntasks=1               # 总任务数
#SBATCH --cpus-per-task=32       # 每个任务的CPU核心数
#SBATCH --mem-per-cpu=20G        # 每个CPU核心的内存
#SBATCH --time=00:10:00          # 总运行时间限制
#SBATCH --mail-type=fail         # 作业失败时发送邮件
#SBATCH --mail-type=end
#SBATCH --mail-type=begin
#SBATCH --mail-user=rl8728@princeton.edu

# 仅重算 dispatch_segmented 电价导出（scripts/export_reconstructed_prices.py），不跑 Snakemake 全量流程。
# 若某年缺少对应的 postnetwork-dispatch-seg-*.nc，将跳过该行并警告。
# STRICT_INPUTS=1 ：任一预期输入缺失则作业失败。

# 配置与版本（VERSION 可通过环境变量覆盖；默认从 config 读取）
CONFIG_PATH="${CONFIG_PATH:-config.yaml}"
VERSION="${VERSION:-}"
if [ -z "$VERSION" ]; then
    VERSION=$(python -c "import yaml;print((yaml.safe_load(open('$CONFIG_PATH', encoding='utf-8')) or {}).get('version',''))" 2>/dev/null)
fi
VERSION="$(echo "$VERSION" | xargs)"
if [ -z "$VERSION" ]; then
    echo "错误: 无法获取版本号。请在 $CONFIG_PATH 中设置 version，或传入环境变量 VERSION。"
    exit 1
fi

# 设置日志文件
LOG_FILE="logs/job_export_prices_${VERSION}_$(date +%Y%m%d_%H%M%S).log"
mkdir -p logs
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== 仅导出重构电价 （export_reconstructed_prices.py） ${VERSION} ==="
echo "开始时间: $(date)"
echo "作业ID: $SLURM_JOB_ID"
echo "节点: $SLURM_NODELIST"
echo "日志文件: $LOG_FILE"
echo "配置文件: $CONFIG_PATH"
echo "版本: $VERSION"
echo

# 加载必要的模块
echo "正在加载模块..."
module purge
module load anaconda3/2024.10
conda activate pypsa
module load gurobi/12.0.0

echo "模块加载完成"
echo

START_TIME=$(date +%s)

export CONFIG_PATH
export VERSION
export STRICT_INPUTS="${STRICT_INPUTS:-0}"

python <<'PY'
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path.cwd().resolve()
config_path = Path(os.environ["CONFIG_PATH"]).expanduser()
if not config_path.is_file():
    print(f"Missing config: {config_path}", file=sys.stderr)
    sys.exit(1)

with config_path.open(encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}

version = (os.environ.get("VERSION") or "").strip() or str(cfg.get("version", "")).strip()
if not version:
    print("No version in config or VERSION env", file=sys.stderr)
    sys.exit(1)

strict = os.environ.get("STRICT_INPUTS", "0").strip() == "1"
results_dir = cfg.get("results_dir") or "results/"
if not str(results_dir).endswith("/"):
    results_dir = f"{results_dir}/"

sc = cfg.get("scenario") or {}
dsp = (cfg.get("dispatch_segmented_prices") or {}).get("price_export") or {}

week_freq = str(dsp.get("week_freq", "W-SUN"))
import_agg = str(dsp.get("import_agg", "min_offer"))
line_cong_eps_mw = float(dsp.get("line_cong_eps_mw", 1e-3))
min_inflow_mw = float(dsp.get("min_inflow_mw", 1e-3))
currency = str(dsp.get("currency", "CNY"))
fx_cny_per_eur = float(dsp.get("fx_cny_per_eur", 7.8))

provinces = None
if cfg.get("using_single_node"):
    provinces = [str(cfg["single_node_province"])]
else:
    rp = cfg.get("reconstruct_prices") or {}
    if rp.get("provinces"):
        provinces = [str(p) for p in rp["provinces"]]


def as_list(x, default=None):
    if x is None:
        return list(default or [])
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


heatings = as_list(sc.get("heating_demand"), ["positive"])
opts_l = as_list(sc.get("opts"), ["ll"])
pathways = as_list(sc.get("pathway"), ["linear2050"])
topologies = as_list(sc.get("topology"), ["current+FCG"])
years = list(sc.get("planning_horizons") or [])

export_script = ROOT / "scripts" / "export_reconstructed_prices.py"
exe = sys.executable
cfg_abs = str(config_path.resolve())

failed = []
missing = []
ran = 0

for hd in heatings:
    for opt in opts_l:
        for pathway in pathways:
            for topo in topologies:
                for y in years:
                    stem = f"{opt}-{topo}-{pathway}-{y}"
                    net = ROOT / f"{results_dir}version-{version}/dispatch_segmented/{hd}/postnetwork-dispatch-seg-{stem}.nc"
                    out = (
                        ROOT
                        / f"{results_dir}version-{version}/prices/dispatch_segmented/{hd}/dispatch_segmented_prices-{stem}.csv"
                    )
                    if not net.is_file():
                        missing.append(str(net))
                        if strict:
                            print(f"STRICT: missing network: {net}", file=sys.stderr)
                            sys.exit(1)
                        print(f"SKIP (missing): {net}")
                        continue
                    cmd = [
                        str(exe),
                        str(export_script),
                        "--network",
                        str(net),
                        "--out",
                        str(out),
                        "--price-mode",
                        "marginal",
                        "--week-freq",
                        week_freq,
                        "--import-agg",
                        import_agg,
                        "--line-cong-eps-mw",
                        str(line_cong_eps_mw),
                        "--min-inflow-mw",
                        str(min_inflow_mw),
                        "--currency",
                        currency,
                        "--fx-cny-per-eur",
                        str(fx_cny_per_eur),
                        "--config",
                        cfg_abs,
                    ]
                    if provinces:
                        for p in provinces:
                            cmd += ["--province", str(p)]
                    print("RUN:", " ".join(cmd))
                    r = subprocess.run(cmd, cwd=str(ROOT))
                    ran += 1
                    if r.returncode != 0:
                        failed.append(str(net))

if ran == 0:
    print("No exports ran (all inputs missing or no planning_horizons).", file=sys.stderr)
    sys.exit(1)
if failed:
    print("Failed:", failed, file=sys.stderr)
    sys.exit(1)
print(f"OK: {ran} export run(s) finished.")
PY

if [ $? -ne 0 ]; then
    echo "✗ export_reconstructed_prices 批量导出失败"
    exit 1
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
echo
echo "✓ ${VERSION} 电价导出完成"
echo "运行时间: $((DURATION / 3600))小时 $((DURATION % 3600 / 60))分钟 $((DURATION % 60))秒"
echo "=== 作业完成 ==="
echo "完成时间: $(date)"
echo "日志文件: $LOG_FILE"
