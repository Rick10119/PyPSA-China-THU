from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pypsa


@dataclass(frozen=True)
class SettlementSummary:
    import_mwh: float
    export_mwh: float
    import_cost: float
    export_revenue: float
    net_settlement_cost: float


def _weighted_energy_mwh(power_mw: pd.Series, weights_h: pd.Series) -> float:
    aligned = power_mw.reindex(weights_h.index).fillna(0.0)
    return float((aligned * weights_h).sum())


def _weighted_cost(power_mw: pd.Series, price: pd.Series, weights_h: pd.Series) -> float:
    aligned_p = power_mw.reindex(weights_h.index).fillna(0.0)
    aligned_c = price.reindex(weights_h.index).astype(float)
    return float((aligned_p * aligned_c * weights_h).sum())


def settlement_summary(n: pypsa.Network) -> SettlementSummary:
    weights_h = n.snapshot_weightings.generators

    import_gens = [g for g in n.generators.index if str(g).startswith("grid_import_")]
    export_gens = [g for g in n.generators.index if str(g).startswith("grid_export_")]

    if not import_gens or not export_gens:
        raise ValueError("Missing settlement generators: expected grid_import_* and grid_export_*")

    if not hasattr(n.generators_t, "p") or n.generators_t.p.empty:
        raise ValueError("No generator dispatch time series found (generators_t.p)")

    if not hasattr(n.generators_t, "marginal_cost") or n.generators_t.marginal_cost.empty:
        raise ValueError("No time-varying marginal costs found (generators_t.marginal_cost)")

    import_p = n.generators_t.p[import_gens].sum(axis=1)
    export_p = n.generators_t.p[export_gens].sum(axis=1)

    # For import, marginal_cost is +price; for export, marginal_cost is -price
    # Recover price from any one import generator (they should all have their own column).
    # Here we compute costs per-generator to avoid assumptions.
    import_cost = 0.0
    for g in import_gens:
        import_cost += _weighted_cost(n.generators_t.p[g], n.generators_t.marginal_cost[g], weights_h)

    export_revenue = 0.0
    for g in export_gens:
        # marginal_cost is -price; revenue is +(price * p)
        export_revenue += _weighted_cost(n.generators_t.p[g], -n.generators_t.marginal_cost[g], weights_h)

    import_mwh = _weighted_energy_mwh(import_p, weights_h)
    export_mwh = _weighted_energy_mwh(export_p, weights_h)
    net_settlement_cost = import_cost - export_revenue

    return SettlementSummary(
        import_mwh=import_mwh,
        export_mwh=export_mwh,
        import_cost=import_cost,
        export_revenue=export_revenue,
        net_settlement_cost=net_settlement_cost,
    )


def validate(n: pypsa.Network) -> None:
    ac_buses = n.buses.index[n.buses.carrier == "AC"]
    for bus in ac_buses:
        if f"grid_import_{bus}" not in n.generators.index:
            raise ValueError(f"Missing grid import generator at bus {bus}")
        if f"grid_export_{bus}" not in n.generators.index:
            raise ValueError(f"Missing grid export generator at bus {bus}")

    # CHP link presence (name-based constraint uses these patterns)
    chp_links = n.links.index[n.links.index.to_series().astype(str).str.contains("CHP")]
    if len(chp_links) == 0:
        raise ValueError("No CHP links found (expected some links with 'CHP' in name if CHP is enabled)")

    # Heat loads exist
    heat_buses = n.buses.index[n.buses.carrier == "heat"]
    heat_loads = n.loads.index[n.loads.bus.isin(heat_buses)]
    if len(heat_loads) == 0:
        raise ValueError("No heat loads found on heat buses")

    # Settlement sanity: export should not exceed import+CHP generation by absurd margin (global check)
    if hasattr(n.links_t, "p1") and not n.links_t.p1.empty:
        chp_gen_links = [l for l in n.links.index if ("CHP" in str(l) and "generator" in str(l))]
        chp_elec = n.links_t.p1[chp_gen_links].sum(axis=1) if chp_gen_links else pd.Series(0.0, index=n.snapshots)
    else:
        chp_elec = pd.Series(0.0, index=n.snapshots)

    weights_h = n.snapshot_weightings.generators
    imp = n.generators_t.p[[g for g in n.generators.index if str(g).startswith("grid_import_")]].sum(axis=1)
    exp = n.generators_t.p[[g for g in n.generators.index if str(g).startswith("grid_export_")]].sum(axis=1)

    lhs = _weighted_energy_mwh(exp, weights_h)
    rhs = _weighted_energy_mwh(imp + chp_elec.clip(lower=0.0), weights_h) + 1e-6
    if lhs > 1.10 * rhs:
        raise ValueError(f"Export energy seems too large: export_MWh={lhs:.2f} vs import+CHP_MWh={rhs:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--network", required=True, help="Path to solved heat-only network .nc")
    args = parser.parse_args()

    n = pypsa.Network(args.network)
    validate(n)
    s = settlement_summary(n)

    print("OK: heat-decoupled validation passed")
    print(f"Settlement import  (MWh): {s.import_mwh:,.2f}")
    print(f"Settlement export  (MWh): {s.export_mwh:,.2f}")
    print(f"Import cost        (cur): {s.import_cost:,.2f}")
    print(f"Export revenue     (cur): {s.export_revenue:,.2f}")
    print(f"Net settlement cost(cur): {s.net_settlement_cost:,.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

