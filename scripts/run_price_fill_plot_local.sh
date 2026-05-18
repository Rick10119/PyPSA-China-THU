#!/usr/bin/env bash
set -euo pipefail

# Local helper:
# 1) optionally clean prices/dispatch_segmented
# 2) rerun export_reconstructed_prices.py only (no snakemake / no re-solve)
# 3) rerun fill + plot scripts
#
# Usage:
#   bash scripts/run_price_fill_plot_local.sh [config_path]
# Example:
#   bash scripts/run_price_fill_plot_local.sh config.yaml
#
# Optional env vars:
#   SKIP_CLEAN=1   # do not delete prices/dispatch_segmented before rerun
#   STRICT_INPUTS=1 # fail if any expected dispatch_segmented .nc is missing

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CONFIG_PATH="${1:-config.yaml}"
SKIP_CLEAN="${SKIP_CLEAN:-0}"
STRICT_INPUTS="${STRICT_INPUTS:-0}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Error: config file not found: $CONFIG_PATH" >&2
  exit 1
fi

CFG_OUTPUT="$(python - "$CONFIG_PATH" <<'PY'
from pathlib import Path
import sys
import yaml

cfg_path = Path(sys.argv[1]).resolve()
cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
results_dir = str(cfg.get("results_dir", "results/")).rstrip("/")
version = str(cfg.get("version", "")).strip()
if not version:
    raise SystemExit("config.yaml missing required key: version")
print(results_dir)
print(version)
PY
)"

RESULTS_DIR="${CFG_OUTPUT%%$'\n'*}"
VERSION="${CFG_OUTPUT#*$'\n'}"
VERSION="${VERSION%%$'\n'*}"
if [[ -z "$RESULTS_DIR" || -z "$VERSION" ]]; then
  echo "Error: failed to parse results_dir/version from $CONFIG_PATH" >&2
  exit 1
fi
PRICE_DIR="${RESULTS_DIR}/version-${VERSION}/prices/dispatch_segmented"

echo "=== Local rerun: export -> fill -> plot ==="
echo "Config:  $CONFIG_PATH"
echo "Version: $VERSION"
echo "Strict missing-input check: $STRICT_INPUTS"
echo

if [[ "$SKIP_CLEAN" != "1" ]]; then
  echo "[1/4] Cleaning price outputs: $PRICE_DIR"
  rm -rf "$PRICE_DIR"
else
  echo "[1/4] SKIP_CLEAN=1, keep existing outputs."
fi

echo "[2/4] Rerun export_reconstructed_prices.py from existing dispatch_segmented .nc"
CONFIG_PATH="$CONFIG_PATH" VERSION="$VERSION" STRICT_INPUTS="$STRICT_INPUTS" python <<'PY'
import os
import subprocess
import sys
from pathlib import Path

import yaml

root = Path.cwd().resolve()
config_path = Path(os.environ["CONFIG_PATH"]).expanduser().resolve()
version = os.environ["VERSION"].strip()
strict = os.environ.get("STRICT_INPUTS", "0").strip() == "1"

cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
results_dir = str(cfg.get("results_dir") or "results/")
if not results_dir.endswith("/"):
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

def as_list(v, default=None):
    if v is None:
        return list(default or [])
    if isinstance(v, (list, tuple)):
        return list(v)
    return [v]

heatings = as_list(sc.get("heating_demand"), ["positive"])
opts_l = as_list(sc.get("opts"), ["ll"])
pathways = as_list(sc.get("pathway"), ["linear2050"])
topologies = as_list(sc.get("topology"), ["current+FCG"])
years = list(sc.get("planning_horizons") or [])

export_script = root / "scripts" / "export_reconstructed_prices.py"
exe = sys.executable

failed = []
ran = 0
for hd in heatings:
    for opt in opts_l:
        for pathway in pathways:
            for topo in topologies:
                for y in years:
                    stem = f"{opt}-{topo}-{pathway}-{y}"
                    net = root / f"{results_dir}version-{version}/dispatch_segmented/{hd}/postnetwork-dispatch-seg-{stem}.nc"
                    out = root / f"{results_dir}version-{version}/prices/dispatch_segmented/{hd}/dispatch_segmented_prices-{stem}.csv"
                    if not net.is_file():
                        msg = f"Missing dispatch .nc: {net}"
                        if strict:
                            raise SystemExit(msg)
                        print(f"SKIP: {msg}")
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
                        str(config_path),
                    ]
                    if provinces:
                        for p in provinces:
                            cmd += ["--province", p]
                    print("RUN:", " ".join(cmd))
                    r = subprocess.run(cmd, cwd=str(root))
                    ran += 1
                    if r.returncode != 0:
                        failed.append(str(net))

if ran == 0:
    raise SystemExit("No exports ran. Check dispatch_segmented outputs and scenario.planning_horizons.")
if failed:
    raise SystemExit(f"Export failed for {len(failed)} network(s).")
print(f"OK: {ran} export run(s) finished.")
PY

echo "[3/4] Fill solar value dataset"
python scripts/fill_solar_value_dataset_2025.py --config "$CONFIG_PATH"

echo "[4/4] Plot yearly solar value factor"
python scripts/plot_solar_value_factor_yearly.py --config "$CONFIG_PATH" --version "$VERSION"

echo
echo "Done."
