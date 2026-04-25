from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa


def _setup_matplotlib_cache(repo_root: Path) -> None:
    os.environ.setdefault("MPLCONFIGDIR", str(repo_root / ".cache" / "matplotlib"))
    os.environ.setdefault("XDG_CACHE_HOME", str(repo_root / ".cache"))


@dataclass(frozen=True)
class TypicalDays:
    peak_day: pd.Timestamp
    low_nonzero_day: pd.Timestamp
    median_day: pd.Timestamp


def _infer_target_province(n: pypsa.Network, preferred: str | None = None) -> str:
    if preferred:
        return preferred
    # Try to infer from heat bus names: "<Province> central heat"/"<Province> decentral heat"
    heat_buses = n.buses.index[n.buses.carrier == "heat"].astype(str)
    provinces = sorted({b.replace(" central heat", "").replace(" decentral heat", "") for b in heat_buses})
    if not provinces:
        raise ValueError("Cannot infer province: no heat buses found")
    return provinces[0]


def _heat_demand_series(n: pypsa.Network, province: str) -> pd.Series:
    buses = [f"{province} central heat", f"{province} decentral heat"]
    loads = n.loads.index[n.loads.bus.isin(buses)]
    if len(loads) == 0:
        raise ValueError(f"No heat loads found for province={province} on buses={buses}")

    # Prefer p_set if available, else p
    if hasattr(n.loads_t, "p_set") and not n.loads_t.p_set.empty:
        ts = n.loads_t.p_set[loads].sum(axis=1)
    elif hasattr(n.loads_t, "p") and not n.loads_t.p.empty:
        ts = n.loads_t.p[loads].sum(axis=1)
    else:
        raise ValueError("No load time series found (loads_t.p_set or loads_t.p)")
    ts.name = "heat_demand_mw"
    return ts


def _heat_supply_breakdown(n: pypsa.Network, province: str) -> pd.DataFrame:
    """
    Returns DataFrame indexed by snapshots with columns as supply categories (MW).
    """
    heat_buses = {f"{province} central heat", f"{province} decentral heat"}

    out = pd.DataFrame(index=n.snapshots)

    # Generators directly on heat buses (e.g. solar thermal)
    gen = n.generators.index[n.generators.bus.isin(heat_buses)]
    if len(gen) > 0 and hasattr(n.generators_t, "p") and not n.generators_t.p.empty:
        g = n.generators_t.p[gen].copy()
        g.columns = n.generators.loc[gen, "carrier"].astype(str).values
        out["gen:solar_thermal"] = g.loc[:, g.columns == "solar thermal"].sum(axis=1) if "solar thermal" in set(g.columns) else 0.0
        other = g.loc[:, ~g.columns.isin(["solar thermal"])].sum(axis=1)
        if not other.empty and float(other.abs().sum()) > 0:
            out["gen:other"] = other

    # Links feeding heat buses: use p1 where bus1 is a heat bus
    if hasattr(n.links_t, "p1") and not n.links_t.p1.empty:
        links_to_heat = n.links.index[n.links.bus1.isin(heat_buses)]
        if len(links_to_heat) > 0:
            p1 = n.links_t.p1[links_to_heat].copy()
            carriers = n.links.loc[links_to_heat, "carrier"].astype(str)
            for carrier in sorted(set(carriers)):
                cols = carriers.index[carriers == carrier]
                out[f"link:{carrier}"] = p1[cols].sum(axis=1)

    # If some links use bus2 for heat (multi-output), include p2 as well
    if hasattr(n.links, "bus2") and hasattr(n.links_t, "p2") and not n.links_t.p2.empty:
        links_to_heat2 = n.links.index[n.links.bus2.isin(heat_buses)]
        if len(links_to_heat2) > 0:
            p2 = n.links_t.p2[links_to_heat2].copy()
            carriers = n.links.loc[links_to_heat2, "carrier"].astype(str)
            for carrier in sorted(set(carriers)):
                cols = carriers.index[carriers == carrier]
                out[f"link2:{carrier}"] = p2[cols].sum(axis=1)

    # Clean: fill missing with 0 and drop all-zero columns
    out = out.fillna(0.0)
    out = out.loc[:, (out.abs().sum(axis=0) > 1e-9)]
    return out


def pick_typical_days(demand_mw: pd.Series) -> TypicalDays:
    daily = demand_mw.resample("D").sum()
    peak_day = daily.idxmax()
    nonzero = daily[daily > 1e-6]
    low_nonzero_day = nonzero.idxmin() if not nonzero.empty else daily.idxmin()
    median_val = float(nonzero.median()) if not nonzero.empty else float(daily.median())
    median_day = (daily - median_val).abs().idxmin()
    return TypicalDays(peak_day=peak_day, low_nonzero_day=low_nonzero_day, median_day=median_day)


def plot_day(
    *,
    demand_mw: pd.Series,
    supply: pd.DataFrame,
    day: pd.Timestamp,
    out_path: Path,
    title: str,
) -> None:
    import matplotlib.pyplot as plt

    start = pd.Timestamp(day).normalize()
    end = start + pd.Timedelta(days=1) - pd.Timedelta(hours=1)

    d = demand_mw.loc[start:end]
    s = supply.loc[start:end]

    fig, ax = plt.subplots(figsize=(12, 5))
    if not s.empty:
        s.plot.area(ax=ax, alpha=0.65, linewidth=0.0)
    d.plot(ax=ax, color="black", linewidth=2.0, label="heat_demand")

    ax.set_title(title)
    ax.set_xlabel("Hour")
    ax.set_ylabel("MW_th")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", ncol=2, fontsize=9)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", required=True, help="Solved heat-only network .nc")
    parser.add_argument("--outdir", required=True, help="Output directory for plots")
    parser.add_argument("--province", default=None, help="Province name (default: infer from network)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    _setup_matplotlib_cache(repo_root)

    n = pypsa.Network(args.network)
    province = _infer_target_province(n, args.province)

    demand = _heat_demand_series(n, province)
    supply = _heat_supply_breakdown(n, province)
    days = pick_typical_days(demand)

    outdir = Path(args.outdir)
    plot_day(
        demand_mw=demand,
        supply=supply,
        day=days.peak_day,
        out_path=outdir / f"{province}-typical-peak.png",
        title=f"{province} typical day (peak demand): {days.peak_day.date()}",
    )
    plot_day(
        demand_mw=demand,
        supply=supply,
        day=days.low_nonzero_day,
        out_path=outdir / f"{province}-typical-low.png",
        title=f"{province} typical day (low nonzero demand): {days.low_nonzero_day.date()}",
    )
    plot_day(
        demand_mw=demand,
        supply=supply,
        day=days.median_day,
        out_path=outdir / f"{province}-typical-median.png",
        title=f"{province} typical day (median demand): {days.median_day.date()}",
    )

    # Also write a small CSV with day selection and annual totals for sanity checks
    summary = pd.DataFrame(
        {
            "province": [province],
            "peak_day": [str(days.peak_day.date())],
            "low_nonzero_day": [str(days.low_nonzero_day.date())],
            "median_day": [str(days.median_day.date())],
            "annual_heat_demand_mwh": [float((demand * n.snapshot_weightings.generators).sum())],
        }
    )
    outdir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(outdir / f"{province}-typical-days-summary.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

