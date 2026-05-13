# SPDX-FileCopyrightText: 2026 PyPSA-China-THU contributors
"""
Provincial electrolytic aluminum (smelter) electricity benchmarks from solved postnetwork .nc.

Uses ``links_t.p0`` (electricity bus power, MW) for all links whose name contains
``aluminum smelter``. Provincial grouping uses the prefix before `` aluminum smelter``,
so both ``Shandong aluminum smelter`` and ``Shandong aluminum smelter line-1`` map to Shandong.

Columns (model-derived benchmarks, not regulatory quotas):

- ``p_nom_sum_MW``: sum of rated electrical capacity (``Link.p_nom``) for smelter links in the province.
- ``peak_hourly_electricity_MW``: maximum hourly provincial aggregate ``p0`` (same province-year series).
- ``annual_electricity_MWh``: sum of hourly aggregate ``p0`` × snapshot weight (typically 8760 h × 1).
- ``mean_hourly_electricity_MW``: annual electricity / sum(weights).

Example::

    python scripts/export_aluminum_provincial_benchmarks.py \\
      --version 0120.1H.1-MMMU-2050-15p \\
      --output-csv results/aluminum_provincial_benchmarks_MMMU_2050_15p.csv

Hourly provincial electricity (MW on ``links_t.p0``, summed per province)::

    python scripts/export_aluminum_provincial_benchmarks.py \\
      --version 0120.1H.1-MMMU-2050-15p \\
      --timeseries-dir results/aluminum_provincial_timeseries
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pypsa


def resolve_postnetwork_nc(version: str, results_root: Path) -> Path:
    base = results_root / f"version-{version}" / "postnetworks" / "positive"
    candidates = [
        base / "postnetwork-ll-current+FCG-linear2050-2050.nc",
        base / "postnetwork-ll-current+Neighbor-linear2050-2050.nc",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def province_from_smelter_link(name: str) -> str:
    if "aluminum smelter" not in name:
        raise ValueError(f"Not an aluminum smelter link name: {name!r}")
    return name.split(" aluminum smelter", 1)[0]


def aluminum_provincial_table(n: pypsa.Network, scenario_label: str) -> pd.DataFrame:
    link_idx = n.links.index[n.links.index.str.contains("aluminum smelter", case=False)]
    if len(link_idx) == 0:
        return pd.DataFrame()

    link_to_prov = {ln: province_from_smelter_link(ln) for ln in link_idx}
    p_nom = n.links.loc[link_idx, "p_nom"]
    p0 = n.links_t.p0[link_idx]

    wcol = "stores" if "stores" in n.snapshot_weightings.columns else n.snapshot_weightings.columns[0]
    weights = n.snapshot_weightings[wcol].reindex(p0.index)

    rows = []
    provinces = sorted(set(link_to_prov.values()))
    for prov in provinces:
        cols = [ln for ln in link_idx if link_to_prov[ln] == prov]
        agg = p0[cols].sum(axis=1).astype(float)
        nom_sum = float(p_nom.loc[cols].sum())
        peak = float(agg.max())
        annual_mwh = float((agg * weights).sum())
        wsum = float(weights.sum())
        mean_mw = annual_mwh / wsum if wsum > 0 else float("nan")
        rows.append(
            {
                "scenario_label": scenario_label,
                "province": prov,
                "n_smelter_links": len(cols),
                "p_nom_sum_MW": nom_sum,
                "peak_hourly_electricity_MW": peak,
                "annual_electricity_MWh": annual_mwh,
                "mean_hourly_electricity_MW": mean_mw,
            }
        )

    nat_agg = p0.sum(axis=1).astype(float)
    nat_nom = float(p_nom.sum())
    nat_peak = float(nat_agg.max())
    nat_ann = float((nat_agg * weights).sum())
    wsum = float(weights.sum())
    rows.append(
        {
            "scenario_label": scenario_label,
            "province": "national",
            "n_smelter_links": len(link_idx),
            "p_nom_sum_MW": nat_nom,
            "peak_hourly_electricity_MW": nat_peak,
            "annual_electricity_MWh": nat_ann,
            "mean_hourly_electricity_MW": nat_ann / wsum if wsum > 0 else float("nan"),
        }
    )

    return pd.DataFrame(rows)


def aluminum_provincial_timeseries_wide(n: pypsa.Network) -> pd.DataFrame:
    """
    Rows: snapshots; columns: each province (sorted) plus ``national``.
    Values: aggregate smelter electricity draw ``p0`` in MW (hourly if snapshots are 1h).
    """
    link_idx = n.links.index[n.links.index.str.contains("aluminum smelter", case=False)]
    if len(link_idx) == 0:
        return pd.DataFrame()

    link_to_prov = {ln: province_from_smelter_link(ln) for ln in link_idx}
    p0 = n.links_t.p0[link_idx].astype(float)

    provinces = sorted(set(link_to_prov.values()))
    data = {}
    for prov in provinces:
        cols = [ln for ln in link_idx if link_to_prov[ln] == prov]
        data[prov] = p0[cols].sum(axis=1)
    data["national"] = p0.sum(axis=1)

    return pd.DataFrame(data)


def _safe_filename_part(label: str) -> str:
    return label.replace("/", "_").replace(" ", "_")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export provincial aluminum smelter electricity benchmarks from postnetwork .nc."
    )
    parser.add_argument("--results-root", type=Path, default=Path("results"))
    parser.add_argument(
        "--version",
        action="append",
        default=[],
        help="Scenario version string (repeatable). Resolves standard postnetwork path.",
    )
    parser.add_argument("--network", action="append", default=[])
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=None,
        help="Optional summary table (benchmarks) CSV.",
    )
    parser.add_argument(
        "--timeseries-dir",
        type=Path,
        default=None,
        help="If set, write one wide CSV per scenario: rows=snapshots, columns=provinces+national (MW).",
    )
    args = parser.parse_args()

    if args.output_csv is None and args.timeseries_dir is None:
        parser.error("Provide --output-csv and/or --timeseries-dir.")

    scenarios: list[tuple[str, Path]] = []
    for v in args.version:
        scenarios.append((v, resolve_postnetwork_nc(v, args.results_root)))
    if args.network:
        if args.label and len(args.label) != len(args.network):
            parser.error("--label count must match --network count when provided.")
        for i, net_path in enumerate(args.network):
            lab = args.label[i] if args.label else Path(net_path).stem
            scenarios.append((lab, Path(net_path)))

    if not scenarios:
        parser.error("Provide at least one --version or --network.")

    parts = []
    for label, nc_path in scenarios:
        if not nc_path.is_file():
            raise FileNotFoundError(f"Network file not found: {nc_path}")
        n = pypsa.Network(str(nc_path))
        if args.timeseries_dir is not None:
            ts = aluminum_provincial_timeseries_wide(n)
            if ts.empty:
                raise ValueError(f"No aluminum smelter links in {nc_path}")
            args.timeseries_dir.mkdir(parents=True, exist_ok=True)
            ts_out = args.timeseries_dir / (
                f"{_safe_filename_part(label)}_aluminum_electricity_MW_wide.csv"
            )
            ts.index.name = "snapshot"
            ts.to_csv(ts_out)
            print(f"Wrote {ts_out} ({len(ts)} rows × {len(ts.columns)} columns)")
        if args.output_csv is not None:
            df = aluminum_provincial_table(n, label)
            df.insert(1, "network_file", str(nc_path.resolve()))
            parts.append(df)

    if args.output_csv is not None:
        out = pd.concat(parts, ignore_index=True)
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.output_csv, index=False)
        pd.set_option("display.max_rows", 40)
        print(out.to_string(index=False))
        print(f"\nWrote {args.output_csv}")


if __name__ == "__main__":
    main()
