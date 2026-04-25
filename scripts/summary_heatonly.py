from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import pypsa


def _weighted_mwh(ts_mw: pd.Series, weights_h: pd.Series) -> float:
    ts_mw = ts_mw.reindex(weights_h.index).fillna(0.0)
    return float((ts_mw * weights_h).sum())


def _heat_bus_sets(province: str | None = None) -> tuple[set[str], set[str], set[str]]:
    if province is None:
        return set(), set(), set()
    central = {f"{province} central heat"}
    decentral = {f"{province} decentral heat"}
    return central | decentral, central, decentral


def summary_heat_shares(n: pypsa.Network, *, province: str | None = None) -> pd.DataFrame:
    weights_h = n.snapshot_weightings.generators

    heat_buses_all = set(n.buses.index[n.buses.carrier == "heat"].astype(str))
    if province is not None:
        heat_buses_all, heat_buses_central, heat_buses_decentral = _heat_bus_sets(province)
    else:
        heat_buses_central = {b for b in heat_buses_all if b.endswith(" central heat")}
        heat_buses_decentral = {b for b in heat_buses_all if b.endswith(" decentral heat")}

    records: list[dict] = []

    def add_energy(carrier: str, mwh_total: float, mwh_central: float, mwh_decentral: float) -> None:
        if abs(mwh_total) < 1e-6 and abs(mwh_central) < 1e-6 and abs(mwh_decentral) < 1e-6:
            return
        records.append(
            {
                "carrier": carrier,
                "mwh_th_total": mwh_total,
                "mwh_th_central": mwh_central,
                "mwh_th_decentral": mwh_decentral,
            }
        )

    # Generators on heat buses
    if hasattr(n, "generators") and not n.generators.empty and hasattr(n.generators_t, "p") and not n.generators_t.p.empty:
        gen_heat = n.generators.index[n.generators.bus.astype(str).isin(heat_buses_all)]
        if len(gen_heat) > 0:
            for carrier, idx in n.generators.loc[gen_heat].groupby("carrier").groups.items():
                p = n.generators_t.p[list(idx)].sum(axis=1)
                mwh_total = _weighted_mwh(p, weights_h)

                gen_central = [g for g in idx if str(n.generators.at[g, "bus"]) in heat_buses_central]
                gen_decentral = [g for g in idx if str(n.generators.at[g, "bus"]) in heat_buses_decentral]
                mwh_central = _weighted_mwh(n.generators_t.p[gen_central].sum(axis=1), weights_h) if gen_central else 0.0
                mwh_decentral = (
                    _weighted_mwh(n.generators_t.p[gen_decentral].sum(axis=1), weights_h) if gen_decentral else 0.0
                )
                add_energy(f"gen:{carrier}", mwh_total, mwh_central, mwh_decentral)

    # Links to heat via bus1 (p1)
    if hasattr(n, "links") and not n.links.empty and hasattr(n.links_t, "p1") and not n.links_t.p1.empty:
        links_to_heat = n.links.index[n.links.bus1.astype(str).isin(heat_buses_all)]
        if len(links_to_heat) > 0:
            for carrier, idx in n.links.loc[links_to_heat].groupby("carrier").groups.items():
                p = -n.links_t.p1[list(idx)].sum(axis=1)
                mwh_total = _weighted_mwh(p, weights_h)

                idx_central = [l for l in idx if str(n.links.at[l, "bus1"]) in heat_buses_central]
                idx_decentral = [l for l in idx if str(n.links.at[l, "bus1"]) in heat_buses_decentral]
                mwh_central = _weighted_mwh(-n.links_t.p1[idx_central].sum(axis=1), weights_h) if idx_central else 0.0
                mwh_decentral = _weighted_mwh(-n.links_t.p1[idx_decentral].sum(axis=1), weights_h) if idx_decentral else 0.0
                add_energy(f"link:{carrier}", mwh_total, mwh_central, mwh_decentral)

    # Links to heat via bus2 (p2)
    if (
        hasattr(n, "links")
        and not n.links.empty
        and "bus2" in n.links.columns
        and hasattr(n.links_t, "p2")
        and not n.links_t.p2.empty
    ):
        links_to_heat2 = n.links.index[n.links.bus2.astype(str).isin(heat_buses_all)]
        if len(links_to_heat2) > 0:
            for carrier, idx in n.links.loc[links_to_heat2].groupby("carrier").groups.items():
                p = -n.links_t.p2[list(idx)].sum(axis=1)
                mwh_total = _weighted_mwh(p, weights_h)

                idx_central = [l for l in idx if str(n.links.at[l, "bus2"]) in heat_buses_central]
                idx_decentral = [l for l in idx if str(n.links.at[l, "bus2"]) in heat_buses_decentral]
                mwh_central = _weighted_mwh(-n.links_t.p2[idx_central].sum(axis=1), weights_h) if idx_central else 0.0
                mwh_decentral = _weighted_mwh(-n.links_t.p2[idx_decentral].sum(axis=1), weights_h) if idx_decentral else 0.0
                add_energy(f"link2:{carrier}", mwh_total, mwh_central, mwh_decentral)

    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df

    df = df.groupby("carrier", as_index=False)[["mwh_th_total", "mwh_th_central", "mwh_th_decentral"]].sum()
    total = df["mwh_th_total"].sum()
    central_total = df["mwh_th_central"].sum()
    decentral_total = df["mwh_th_decentral"].sum()

    df["share_total"] = df["mwh_th_total"] / total if total > 0 else 0.0
    df["share_central"] = df["mwh_th_central"] / central_total if central_total > 0 else 0.0
    df["share_decentral"] = df["mwh_th_decentral"] / decentral_total if decentral_total > 0 else 0.0
    return df.sort_values("mwh_th_total", ascending=False).reset_index(drop=True)


def _get_opt_or_nom(df: pd.DataFrame, opt_col: str, nom_col: str) -> pd.Series:
    if opt_col in df.columns and pd.api.types.is_numeric_dtype(df[opt_col]):
        return df[opt_col]
    if nom_col in df.columns and pd.api.types.is_numeric_dtype(df[nom_col]):
        return df[nom_col]
    return pd.Series(0.0, index=df.index, dtype=float)


def summary_capacities(n: pypsa.Network) -> pd.DataFrame:
    records: list[dict] = []

    if hasattr(n, "generators") and not n.generators.empty:
        cap = _get_opt_or_nom(n.generators, "p_nom_opt", "p_nom")
        g = n.generators.copy()
        g["_cap"] = cap.fillna(0.0).astype(float)
        agg = g.groupby("carrier").agg(capacity=("_cap", "sum"), count=("_cap", "size")).reset_index()
        for _, row in agg.iterrows():
            records.append(
                {
                    "component": "Generator",
                    "carrier": str(row["carrier"]),
                    "unit": "MW",
                    "capacity": float(row["capacity"]),
                    "count": int(row["count"]),
                }
            )

    if hasattr(n, "links") and not n.links.empty:
        cap = _get_opt_or_nom(n.links, "p_nom_opt", "p_nom")
        l = n.links.copy()
        l["_cap"] = cap.fillna(0.0).astype(float)
        agg = l.groupby("carrier").agg(capacity=("_cap", "sum"), count=("_cap", "size")).reset_index()
        for _, row in agg.iterrows():
            records.append(
                {
                    "component": "Link",
                    "carrier": str(row["carrier"]),
                    "unit": "MW",
                    "capacity": float(row["capacity"]),
                    "count": int(row["count"]),
                }
            )

    if hasattr(n, "stores") and not n.stores.empty:
        cap = _get_opt_or_nom(n.stores, "e_nom_opt", "e_nom")
        s = n.stores.copy()
        s["_cap"] = cap.fillna(0.0).astype(float)
        agg = s.groupby("carrier").agg(capacity=("_cap", "sum"), count=("_cap", "size")).reset_index()
        for _, row in agg.iterrows():
            records.append(
                {
                    "component": "Store",
                    "carrier": str(row["carrier"]),
                    "unit": "MWh",
                    "capacity": float(row["capacity"]),
                    "count": int(row["count"]),
                }
            )

    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    return df.sort_values(["component", "capacity"], ascending=[True, False]).reset_index(drop=True)


def summary_co2(n: pypsa.Network) -> pd.DataFrame:
    if "co2_emissions" not in n.carriers.columns:
        total = gen_total = link_total = 0.0
    else:
        weights_h = n.snapshot_weightings.generators
        ce = n.carriers.co2_emissions.fillna(0.0)

        gen_total = 0.0
        link_total = 0.0

        if hasattr(n, "generators") and not n.generators.empty and hasattr(n.generators_t, "p") and not n.generators_t.p.empty:
            g = n.generators.copy()
            g["co2_emissions"] = g.carrier.map(ce).fillna(0.0)
            g = g[g.co2_emissions != 0]
            if not g.empty:
                energy = n.generators_t.p[g.index].mul(weights_h, axis=0).sum()
                gen_total = float((energy * g.co2_emissions).sum())

        if hasattr(n, "links") and not n.links.empty and hasattr(n.links_t, "p0") and not n.links_t.p0.empty:
            l = n.links.copy()
            l["co2_emissions"] = l.carrier.map(ce).fillna(0.0)
            l = l[l.co2_emissions != 0]
            if not l.empty:
                energy = n.links_t.p0[l.index].mul(weights_h, axis=0).sum()
                link_total = float((energy * l.co2_emissions).sum())

        total = gen_total + link_total

    limit = None
    if hasattr(n, "global_constraints") and "co2_limit" in n.global_constraints.index:
        try:
            limit = float(n.global_constraints.at["co2_limit", "constant"])
        except Exception:
            limit = None

    return pd.DataFrame(
        [
            {
                "total_co2": total,
                "gen_co2": gen_total,
                "link_co2": link_total,
                "co2_limit_constant": limit,
            }
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Heat-only post-solve summaries (shares, capacities, CO2).")
    parser.add_argument("--network", required=True, help="Solved heat-only network .nc")
    parser.add_argument("--outdir", required=True, help="Output directory (will be created)")
    parser.add_argument("--province", default=None, help="Optional: restrict heat shares to one province")
    args = parser.parse_args()

    n = pypsa.Network(args.network)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    summary_heat_shares(n, province=args.province).to_csv(outdir / "heat-shares.csv", index=False)
    summary_capacities(n).to_csv(outdir / "capacities.csv", index=False)
    summary_co2(n).to_csv(outdir / "co2.csv", index=False)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

