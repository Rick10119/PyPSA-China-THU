#!/usr/bin/env python3
"""Plot provincial-mean solar value_factor over planning years from solar_value_dataset.xlsx.

Default paths use ``version`` and ``results_dir`` from the repo ``config.yaml`` (same layout as
``fill_solar_value_dataset_2025.py``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CONFIG = ROOT / "config.yaml"


def _version_dir_from_config(config_path: Path, version_override: str | None) -> tuple[Path, str]:
    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with config_path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    root = config_path.parent.resolve()
    results_rel = str(cfg.get("results_dir") or "results/")
    if version_override is not None and str(version_override).strip():
        version = str(version_override).strip()
    else:
        v = cfg.get("version")
        if v is None:
            raise KeyError("config.yaml must define 'version', or pass --version")
        version = str(v).strip()
    version_dir = (root / Path(results_rel) / f"version-{version}").resolve()
    return version_dir, version


def _configure_cjk_font() -> None:
    if sys.platform == "darwin":
        plt.rcParams["font.sans-serif"] = [
            "PingFang SC",
            "Heiti SC",
            "Songti SC",
            "Arial Unicode MS",
            "sans-serif",
        ]
    else:
        plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "WenQuanYi Zen Hei", "sans-serif"]
    plt.rcParams["axes.unicode_minus"] = False


def _province_key(zone: str) -> str:
    if zone in ("EastInnerMongolia", "WestInnerMongolia"):
        return "InnerMongolia"
    return zone


def main() -> None:
    _configure_cjk_font()
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"config.yaml with version and results_dir (default: {_DEFAULT_CONFIG})",
    )
    ap.add_argument(
        "--version",
        default=None,
        help="Override config version id (default: read config 'version')",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for PNG/PDF (default: <version_dir>/figures)",
    )
    args = ap.parse_args()

    version_dir, version = _version_dir_from_config(args.config.resolve(), args.version)
    xlsx = version_dir / "solar_value_dataset.xlsx"
    out_dir = args.output_dir or (version_dir / "figures")
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(xlsx, sheet_name="Sheet1", header=0)
    df = df.assign(province=df["load_zone"].map(_province_key))
    by_prov = df.groupby(["year", "province"], as_index=False)["value_factor"].mean()
    national = by_prov.groupby("year")["value_factor"].agg(["mean", "std", "min", "max"]).reset_index()

    pivot = by_prov.pivot(index="province", columns="year", values="value_factor").sort_index()

    fig = plt.figure(figsize=(11, 5.2))
    gs = fig.add_gridspec(1, 2, width_ratios=[1.15, 1.0], wspace=0.28)

    ax0 = fig.add_subplot(gs[0, 0])
    years = national["year"].values
    m = national["mean"].values
    sd = national["std"].replace(np.nan, 0).values
    ax0.plot(years, m, "o-", color="#2563eb", linewidth=2.2, markersize=7, label="各省算术平均")
    ax0.fill_between(years, m - sd, m + sd, color="#2563eb", alpha=0.18, label="±1σ（省间离散）")
    ax0.axhline(1.0, color="#94a3b8", linestyle="--", linewidth=1, zorder=0)
    ax0.set_xlabel("年")
    ax0.set_ylabel("Value factor")
    ax0.set_title(f"solar_value_dataset — 各省平均 VF 逐年变化\nversion {version}")
    ax0.set_xticks(years)
    ax0.legend(loc="upper right", framealpha=0.92)
    ax0.grid(True, alpha=0.35)
    ax0.set_ylim(bottom=min(0.82, float(m.min() - 0.04)), top=max(1.06, float(m.max() + 0.04)))

    ax1 = fig.add_subplot(gs[0, 1])
    im = ax1.imshow(
        pivot.values,
        aspect="auto",
        cmap="RdYlGn",
        vmin=0.75,
        vmax=1.15,
        interpolation="nearest",
    )
    ax1.set_yticks(range(len(pivot.index)))
    ax1.set_yticklabels(pivot.index, fontsize=7)
    ax1.set_xticks(range(len(pivot.columns)))
    ax1.set_xticklabels([str(int(c)) for c in pivot.columns], rotation=45, ha="right")
    ax1.set_xlabel("年")
    ax1.set_title("分省 Value factor\n内蒙古东西合并为 InnerMongolia")
    fig.colorbar(im, ax=ax1, fraction=0.046, pad=0.04, label="value_factor")

    png = out_dir / "solar_value_factor_yearly_provincial_mean.png"
    pdf = out_dir / "solar_value_factor_yearly_provincial_mean.pdf"
    fig.savefig(png, dpi=160, bbox_inches="tight")
    fig.savefig(pdf, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {png}\nWrote {pdf}")


if __name__ == "__main__":
    main()
