# SPDX-FileCopyrightText: 2026 Ruike Lyu
#
# SPDX-License-Identifier: MIT
"""Prepare Shandong negative-price study inputs without running dispatch solves.

The script extracts Shandong capacities from solved anchor-year postnetworks in
`results/version-0609.1H.1`, linearly interpolates study-year capacities, and
writes compact CSVs for review. It intentionally does not modify `.nc` networks
or invoke the optimizer.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


DEFAULT_VERSION = "0609.1H.1"
DEFAULT_PROVINCE = "Shandong"
DEFAULT_ANCHOR_YEARS = [2025, 2030, 2035]
DEFAULT_TARGET_YEARS = list(range(2027, 2036))
DEFAULT_NETWORK_PATTERN = (
    "results/version-{version}/postnetworks/positive/"
    "postnetwork-ll-current+FCG-linear2050-{year}.nc"
)
DEFAULT_OUTDIR = Path("data/shandong_negative_price")
DEFAULT_NET_RECEIVING_TWH = 150.0


GENERATOR_RENEWABLE_CARRIERS = {"onwind", "offwind", "solar"}
GENERATOR_THERMAL_CARRIERS = {"coal power plant", "coal cc", "nuclear", "hydroelectricity"}
LINK_CAPACITY_CARRIERS = {"battery", "OCGT gas", "CHP coal", "CHP gas"}


def _read_network(path: Path):
    try:
        import pypsa
    except ImportError as exc:  # pragma: no cover - environment guidance
        raise SystemExit(
            "pypsa is required. Run with the project environment, e.g. "
            "`conda run -n pypsa python scripts/prepare_shandong_negative_price_inputs.py`."
        ) from exc
    return pypsa.Network(path)


def _nominal(row: pd.Series, base_col: str = "p_nom") -> float:
    opt_col = f"{base_col}_opt"
    value = row.get(opt_col, row.get(base_col, 0.0))
    if pd.isna(value):
        value = row.get(base_col, 0.0)
    return float(value or 0.0)


def _extract_capacity_rows(n, *, province: str, year: int) -> list[dict]:
    rows: list[dict] = []

    if hasattr(n, "generators") and not n.generators.empty:
        gens = n.generators
        mask = gens["bus"].astype(str).str.startswith(province)
        for name, row in gens.loc[mask].iterrows():
            carrier = str(row.get("carrier", ""))
            if carrier in GENERATOR_RENEWABLE_CARRIERS:
                group = "renewable"
            elif carrier in GENERATOR_THERMAL_CARRIERS:
                group = "generator"
            else:
                continue
            rows.append(
                {
                    "year": year,
                    "province": province,
                    "component": "Generator",
                    "name": str(name),
                    "carrier": carrier,
                    "group": group,
                    "capacity_mw": _nominal(row),
                }
            )

    if hasattr(n, "links") and not n.links.empty:
        links = n.links
        mask = links["bus1"].astype(str).str.startswith(province) | links["bus0"].astype(str).str.startswith(province)
        for name, row in links.loc[mask].iterrows():
            carrier = str(row.get("carrier", ""))
            if carrier not in LINK_CAPACITY_CARRIERS:
                continue
            rows.append(
                {
                    "year": year,
                    "province": province,
                    "component": "Link",
                    "name": str(name),
                    "carrier": carrier,
                    "group": "link_capacity",
                    "capacity_mw": _nominal(row),
                }
            )

    if hasattr(n, "storage_units") and not n.storage_units.empty:
        su = n.storage_units
        mask = su["bus"].astype(str).str.startswith(province)
        for name, row in su.loc[mask].iterrows():
            rows.append(
                {
                    "year": year,
                    "province": province,
                    "component": "StorageUnit",
                    "name": str(name),
                    "carrier": str(row.get("carrier", "")),
                    "group": "storage",
                    "capacity_mw": _nominal(row),
                }
            )

    return rows


def _interpolate_by_carrier(anchor: pd.DataFrame, target_years: list[int]) -> pd.DataFrame:
    grouped = (
        anchor.groupby(["province", "component", "carrier", "group", "year"], as_index=False)["capacity_mw"]
        .sum()
        .sort_values(["province", "component", "carrier", "group", "year"])
    )
    out: list[pd.DataFrame] = []
    for keys, df in grouped.groupby(["province", "component", "carrier", "group"], sort=False):
        s = df.set_index("year")["capacity_mw"].sort_index()
        years = sorted(set(s.index.astype(int)).union(target_years))
        interp = s.reindex(years).interpolate(method="index").reindex(target_years)
        part = pd.DataFrame(
            {
                "province": keys[0],
                "component": keys[1],
                "carrier": keys[2],
                "group": keys[3],
                "year": target_years,
                "capacity_mw": interp.to_numpy(dtype=float),
            }
        )
        out.append(part)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _add_penetration_metrics(interp: pd.DataFrame) -> pd.DataFrame:
    piv = interp.pivot_table(index="year", columns="carrier", values="capacity_mw", aggfunc="sum").fillna(0.0)
    wind = piv.get("onwind", 0.0) + piv.get("offwind", 0.0)
    solar = piv.get("solar", 0.0)
    total = piv.sum(axis=1).replace(0.0, pd.NA)
    summary = pd.DataFrame(
        {
            "year": piv.index.astype(int),
            "wind_capacity_mw": wind.to_numpy(dtype=float),
            "solar_capacity_mw": solar.to_numpy(dtype=float),
            "vre_capacity_mw": (wind + solar).to_numpy(dtype=float),
            "total_tracked_capacity_mw": total.astype(float).to_numpy(),
            "vre_capacity_penetration": ((wind + solar) / total).astype(float).to_numpy(),
        }
    )
    return summary


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", default=DEFAULT_VERSION)
    p.add_argument("--province", default=DEFAULT_PROVINCE)
    p.add_argument("--anchor-years", nargs="+", type=int, default=DEFAULT_ANCHOR_YEARS)
    p.add_argument("--target-years", nargs="+", type=int, default=DEFAULT_TARGET_YEARS)
    p.add_argument("--network-pattern", default=DEFAULT_NETWORK_PATTERN)
    p.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    p.add_argument(
        "--net-receiving-twh",
        type=float,
        default=DEFAULT_NET_RECEIVING_TWH,
        help="Annual Shandong net received electricity to subtract from load, in TWh/year.",
    )
    p.add_argument(
        "--allocation-exponent",
        type=float,
        default=2.0,
        help="Exponent applied to wind/PV available output when allocating net received electricity.",
    )
    return p.parse_args()


def _net_import_load_deduction(province: str, annual_twh: float) -> pd.DataFrame:
    avg_mw = float(annual_twh) * 1e6 / 8760.0
    return pd.DataFrame(
        [
            {
                "province": province,
                "annual_net_receiving_twh": float(annual_twh),
                "annual_net_receiving_100m_kwh": float(annual_twh) * 10.0,
                "flat_load_deduction_mw": avg_mw,
                "allocation": "flat",
                "note": "Subtract from Shandong AC load before dispatch solve.",
            }
        ]
    )


def _hourly_vre_available_deduction(
    n,
    *,
    province: str,
    year: int,
    annual_twh: float,
    allocation_exponent: float,
) -> pd.DataFrame:
    gens = n.generators
    idx = gens.index[
        gens["carrier"].astype(str).isin(GENERATOR_RENEWABLE_CARRIERS)
        & gens["bus"].astype(str).map(lambda b: str(b).split(" ", 1)[0] == province)
    ].tolist()
    snapshots = pd.Index(n.snapshots)
    weights = n.snapshot_weightings.generators if "generators" in n.snapshot_weightings else n.snapshot_weightings.objective
    weights = pd.to_numeric(weights, errors="coerce").reindex(snapshots).fillna(1.0).astype(float)
    if idx:
        cap = gens.loc[idx].apply(lambda r: _nominal(r), axis=1).astype(float)
        if hasattr(n, "generators_t") and hasattr(n.generators_t, "p_max_pu"):
            p_max_pu = n.generators_t.p_max_pu.reindex(index=snapshots, columns=idx).fillna(1.0).astype(float)
        else:
            p_max_pu = pd.DataFrame(1.0, index=snapshots, columns=idx)
        available = p_max_pu.mul(cap, axis=1).clip(lower=0.0).sum(axis=1)
    else:
        available = pd.Series(0.0, index=snapshots, dtype=float)
    allocation_key = available.clip(lower=0.0) ** float(allocation_exponent)
    total_available_mwh = float((allocation_key * weights).sum())
    target_mwh = float(annual_twh) * 1e6
    if total_available_mwh > 1e-9:
        deduction_mw = allocation_key * (target_mwh / total_available_mwh)
    else:
        deduction_mw = pd.Series(target_mwh / max(float(weights.sum()), 1e-9), index=snapshots)
    return pd.DataFrame(
        {
            "snapshot": snapshots,
            "year": year,
            "province": province,
            "vre_available_mw": available.to_numpy(dtype=float),
            "load_deduction_mw": deduction_mw.to_numpy(dtype=float),
            "snapshot_weight_h": weights.to_numpy(dtype=float),
            "allocation": "vre_available",
            "allocation_exponent": float(allocation_exponent),
        }
    )


def main() -> None:
    args = parse_args()
    args.outdir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    hourly_deduction_parts: list[pd.DataFrame] = []
    for year in args.anchor_years:
        path = Path(args.network_pattern.format(version=args.version, year=year))
        if not path.exists():
            raise FileNotFoundError(f"Missing anchor-year network: {path}")
        n = _read_network(path)
        rows.extend(_extract_capacity_rows(n, province=args.province, year=year))
        hourly_deduction_parts.append(
            _hourly_vre_available_deduction(
                n,
                province=args.province,
                year=year,
                annual_twh=args.net_receiving_twh,
                allocation_exponent=args.allocation_exponent,
            )
        )

    anchor = pd.DataFrame(rows)
    if anchor.empty:
        raise RuntimeError(f"No {args.province} capacity rows found in anchor networks.")

    interp = _interpolate_by_carrier(anchor, args.target_years)
    summary = _add_penetration_metrics(interp)
    deduction = _net_import_load_deduction(args.province, args.net_receiving_twh)
    hourly_deduction = pd.concat(hourly_deduction_parts, ignore_index=True)

    anchor_out = args.outdir / "anchor_capacities_by_asset.csv"
    interp_out = args.outdir / "interpolated_capacities_by_carrier.csv"
    summary_out = args.outdir / "study_year_capacity_summary.csv"
    deduction_out = args.outdir / "net_import_load_deduction.csv"
    hourly_deduction_out = args.outdir / "net_import_load_deduction_hourly_vre_available.csv"
    anchor.to_csv(anchor_out, index=False)
    interp.to_csv(interp_out, index=False)
    summary.to_csv(summary_out, index=False)
    deduction.to_csv(deduction_out, index=False)
    hourly_deduction.to_csv(hourly_deduction_out, index=False)

    print(f"Wrote {anchor_out}")
    print(f"Wrote {interp_out}")
    print(f"Wrote {summary_out}")
    print(f"Wrote {deduction_out}")
    print(f"Wrote {hourly_deduction_out}")


if __name__ == "__main__":
    main()
