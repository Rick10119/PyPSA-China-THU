# SPDX-FileCopyrightText: 2026 Ruike Lyu
#
# SPDX-License-Identifier: MIT
"""
Second-stage economic dispatch on a solved postnetwork.

1) Freeze all nominal capacities at optimized values and disable investment.
2) Optionally replace coal/gas *main* plant components with parallel capacity blocks
   (piecewise-constant marginal costs). CHP links are not split (v1).
3) Re-solve as a pure dispatch LP and export `postnetwork-dispatch-seg-*.nc`.

Price export: use `export_reconstructed_prices.py --price-mode marginal` on this
`.nc` output. Do not use `--price-mode mapped` on top of segmented dispatch (double bid).

Configurable defaults live in ``config.yaml`` under ``dispatch_segmented_prices`` (authoritative). As of repo
baseline: five identical ``shares`` for coal/gas blocks ``[0.50, 0.20, 0.15, 0.10, 0.05]``, with
EUR/MWh full-stack bids (when ``zero_gas_fuel_marginal_cost`` is True) documented in README_cursor.md
(coal ``[0, 45, 55, 75, 192]``, gas ``[0, 85, 100, 120, 192]``).

Snakemake rule: `run_dispatch_segmented`.
"""

import logging
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pypsa

_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from _helpers import configure_logging, override_component_attrs  # noqa: E402
from solve_network_myopic import add_chp_constraints, add_transimission_constraints, prepare_network  # noqa: E402

logger = logging.getLogger(__name__)


def extra_functionality_dispatch(n, snapshots):
    """CHP + asymmetric transmission pairs; no planning-year retrofit constraints."""
    add_chp_constraints(n)
    add_transimission_constraints(n)


def _ac_energy_balance_gwh(n: pypsa.Network) -> float:
    """
    Return AC-bus net energy balance (GWh) from exported/loaded network time series.
    Near-zero means balanced. Large |value| indicates inconsistent AC accounting.
    """
    eb = n.statistics.energy_balance(aggregate_time="sum")
    if isinstance(eb, pd.Series):
        if "bus_carrier" in eb.index.names:
            ac = eb[eb.index.get_level_values("bus_carrier") == "AC"]
        else:
            ac = eb
    else:
        # Defensive path for older/newer API shapes.
        if "bus_carrier" in eb.columns:
            ac = eb.loc[eb["bus_carrier"] == "AC", "value"]
        else:
            ac = eb["value"] if "value" in eb.columns else pd.Series(dtype=float)
    return float(ac.sum() / 1000.0)


def _log_ac_balance(n: pypsa.Network, stage: str, tol_gwh: float = 1e-3) -> None:
    try:
        gap = _ac_energy_balance_gwh(n)
    except Exception as e:  # pragma: no cover - diagnostics only
        logger.warning("AC energy-balance check failed at %s: %s", stage, e)
        return
    level = logger.warning if abs(gap) > tol_gwh else logger.info
    level("AC energy-balance gap at %s: %.6f GWh", stage, gap)


def _nom_opt_value(row, nom_col: str, opt_col: str) -> float:
    v = float(row.get(nom_col, 0.0) or 0.0)
    if opt_col in row.index and pd.notna(row[opt_col]):
        v = max(v, float(row[opt_col]))
    return v


def freeze_capacities_and_zero_capex(n: pypsa.Network, zero_capital_cost: bool = True) -> None:
    """Set nominal capacities to solved optima, disable expansion, optionally zero capex."""
    for component in n.iterate_components():
        name = component.name
        df = component.df
        if name == "Store":
            nom, opt, ext = "e_nom", "e_nom_opt", "e_nom_extendable"
        elif name == "Line":
            nom, opt, ext = "s_nom", "s_nom_opt", "s_nom_extendable"
        elif name in ("Generator", "Link", "StorageUnit"):
            nom, opt, ext = "p_nom", "p_nom_opt", "p_nom_extendable"
        else:
            continue
        if ext not in df.columns:
            continue
        for idx in df.index:
            row = df.loc[idx]
            new_nom = _nom_opt_value(row, nom, opt)
            df.at[idx, nom] = new_nom
            df.at[idx, ext] = False
            mx = nom.replace("nom", "nom_max")
            if mx in df.columns:
                df.at[idx, mx] = np.inf
            mn = nom.replace("nom", "nom_min")
            if mn in df.columns:
                old_mn = df.at[idx, mn]
                try:
                    old_mn_f = float(old_mn)
                    if np.isnan(old_mn_f):
                        old_mn_f = 0.0
                except Exception:
                    old_mn_f = 0.0
                df.at[idx, mn] = min(old_mn_f, float(df.at[idx, nom]))

    if zero_capital_cost:
        for component in n.iterate_components():
            if "capital_cost" in component.df.columns:
                component.df["capital_cost"] = 0.0


def _add_kwargs_filtered(n: pypsa.Network, component: str, name: str, attrs: dict) -> None:
    """Drop NaN/None so PyPSA `add` does not receive invalid optional fields."""
    clean = {}
    for k, v in attrs.items():
        if v is None:
            continue
        if isinstance(v, (float, np.floating)) and np.isnan(v):
            continue
        clean[k] = v
    n.add(component, name, **clean)


def split_generators_carrier(
    n: pypsa.Network,
    carrier: str,
    shares: list[float],
    marginal_costs: list[float],
) -> None:
    if len(shares) != len(marginal_costs):
        raise ValueError("shares and marginal_costs must have same length")
    if abs(sum(shares) - 1.0) > 1e-6:
        raise ValueError(f"shares must sum to 1, got {sum(shares)}")
    targets = n.generators[n.generators["carrier"] == carrier].index.tolist()
    for idx in targets:
        row = n.generators.loc[idx]
        p_total = float(row["p_nom"])
        if p_total < 1e-9:
            continue
        saved_ts: dict[str, pd.Series] = {}
        for attr in ("p_max_pu", "p_min_pu", "p"):
            pnl = getattr(n, "generators_t", None)
            if pnl is not None and hasattr(pnl, attr):
                df = getattr(pnl, attr)
                if idx in df.columns:
                    saved_ts[attr] = df[idx].copy()
        static = row.drop(
            labels=[
                "p_nom",
                "marginal_cost",
                "capital_cost",
                "p_nom_opt",
                "p_nom_extendable",
                "p_nom_min",
                "p_nom_max",
            ],
            errors="ignore",
        ).to_dict()
        static["p_nom_extendable"] = False
        static["capital_cost"] = 0.0
        static["carrier"] = row["carrier"]
        static["bus"] = row["bus"]
        new_names = [f"{idx}__seg{k}" for k in range(len(shares))]
        n.mremove("Generator", [idx])
        for k, nn in enumerate(new_names):
            static_k = dict(static)
            static_k["p_nom"] = p_total * float(shares[k])
            static_k["marginal_cost"] = float(marginal_costs[k])
            _add_kwargs_filtered(n, "Generator", nn, static_k)
        for attr, series in saved_ts.items():
            df = getattr(n.generators_t, attr)
            for nn in new_names:
                df[nn] = series.values


def split_links_carrier(
    n: pypsa.Network,
    carrier: str,
    shares: list[float],
    marginal_costs: list[float],
    *,
    only_bus1_carrier: str | None = None,
) -> None:
    if len(shares) != len(marginal_costs):
        raise ValueError("shares and marginal_costs must have same length")
    if abs(sum(shares) - 1.0) > 1e-6:
        raise ValueError(f"shares must sum to 1, got {sum(shares)}")
    targets = n.links[n.links["carrier"] == carrier].index.tolist()
    if only_bus1_carrier is not None and hasattr(n, "buses") and not n.buses.empty:
        want = str(only_bus1_carrier)
        keep = []
        for i in targets:
            try:
                b1 = n.links.at[i, "bus1"]
                if str(n.buses.at[b1, "carrier"]) == want:
                    keep.append(i)
            except Exception:
                continue
        targets = keep
    for idx in targets:
        row = n.links.loc[idx]
        p_total = float(row["p_nom"])
        if p_total < 1e-9:
            continue
        saved_ts: dict[str, pd.Series] = {}
        for attr in ("p_max_pu", "p_min_pu", "p0", "p1", "p2", "p3"):
            pnl = getattr(n, "links_t", None)
            if pnl is not None and hasattr(pnl, attr):
                df = getattr(pnl, attr)
                if idx in df.columns:
                    saved_ts[attr] = df[idx].copy()
        drop_labels = [
            "p_nom",
            "marginal_cost",
            "capital_cost",
            "p_nom_opt",
            "p_nom_extendable",
            "p_nom_min",
            "p_nom_max",
        ]
        static = row.drop(labels=drop_labels, errors="ignore").to_dict()
        static["p_nom_extendable"] = False
        static["capital_cost"] = 0.0
        static["carrier"] = row["carrier"]
        new_names = [f"{idx}__seg{k}" for k in range(len(shares))]
        n.mremove("Link", [idx])
        for k, nn in enumerate(new_names):
            static_k = dict(static)
            static_k["p_nom"] = p_total * float(shares[k])
            static_k["marginal_cost"] = float(marginal_costs[k])
            _add_kwargs_filtered(n, "Link", nn, static_k)
        for attr, series in saved_ts.items():
            df = getattr(n.links_t, attr)
            for nn in new_names:
                df[nn] = series.values


def apply_segmented_carriers(n: pypsa.Network, carriers_cfg: dict) -> None:
    if not carriers_cfg:
        return
    gen_map = carriers_cfg.get("Generator") or {}
    for carrier, spec in gen_map.items():
        split_generators_carrier(
            n,
            str(carrier),
            list(spec["shares"]),
            list(spec["marginal_cost"]),
        )
    link_map = carriers_cfg.get("Link") or {}
    for carrier, spec in link_map.items():
        split_links_carrier(
            n,
            str(carrier),
            list(spec["shares"]),
            list(spec["marginal_cost"]),
            only_bus1_carrier=(spec.get("only_bus1_carrier") if isinstance(spec, dict) else None),
        )


def zero_gas_fuel_marginal_cost(n: pypsa.Network) -> None:
    m = n.generators.index.astype(str).str.endswith(" gas fuel")
    if m.any():
        n.generators.loc[m, "marginal_cost"] = 0.0


def solve_dispatch_lp(n: pypsa.Network, config: dict, solving: dict, opts) -> tuple[str, str]:
    if isinstance(opts, str):
        opts_list = [o for o in opts.split("-") if o]
    else:
        opts_list = list(opts)
    set_of_options = solving["solver"]["options"]
    solver_options = solving["solver_options"][set_of_options] if set_of_options else {}
    solver_name = solving["solver"]["name"]
    cf_solving = solving["options"]
    track_iterations = cf_solving.get("track_iterations", False)
    min_iterations = cf_solving.get("min_iterations", 4)
    max_iterations = cf_solving.get("max_iterations", 6)
    n.config = config
    n.opts = opts_list
    skip_iterations = cf_solving.get("skip_iterations", False)
    # No AC `Line`s (only `Link`-based topology): iterative line expansion API must not run.
    if hasattr(n, "lines") and n.lines.empty:
        skip_iterations = True
    elif hasattr(n, "lines") and not n.lines.empty and not n.lines.s_nom_extendable.any():
        skip_iterations = True
    optimize_kwargs = {}
    if skip_iterations:
        status, condition = n.optimize(
            solver_name=solver_name,
            solver_options=solver_options,
            extra_functionality=extra_functionality_dispatch,
            **optimize_kwargs,
        )
    else:
        status, condition = n.optimize.optimize_transmission_expansion_iteratively(
            solver_name=solver_name,
            solver_options=solver_options,
            track_iterations=track_iterations,
            min_iterations=min_iterations,
            max_iterations=max_iterations,
            extra_functionality=extra_functionality_dispatch,
            **optimize_kwargs,
        )
    if status != "ok":
        logger.warning("Dispatch solve status=%s condition=%s", status, condition)
        raise RuntimeError(f"Dispatch optimization failed: status={status} condition={condition}")
    obj = None
    if hasattr(n.model, "objective_value"):
        obj = float(n.model.objective_value)
    elif hasattr(n.model, "objective"):
        obj = float(n.model.objective.value)
    if obj is not None:
        try:
            n.meta["objective_dispatch_segmented"] = obj
        except Exception:
            pass
    return status, str(condition)


def run(
    *,
    network_in: str,
    network_out: str,
    config: dict,
    solving: dict,
    opts: str,
    solve_opts: dict,
    using_single_node: bool,
    single_node_province: str,
    overrides_path: str | None,
    dispatch_cfg: dict,
) -> None:
    if overrides_path:
        overrides = override_component_attrs(overrides_path)
        n = pypsa.Network(network_in, override_component_attrs=overrides)
    else:
        n = pypsa.Network(network_in)
    n = prepare_network(
        n,
        solve_opts,
        using_single_node=using_single_node,
        single_node_province=single_node_province,
    )
    # If we truncate snapshots for a smoke-test (nhours), keep global annual constraints
    # (e.g. `co2_limit`) but scale them to the truncated horizon to avoid artificial
    # infeasibility caused by repeating the first hours across the year.
    if solve_opts.get("nhours"):
        nh = int(solve_opts["nhours"])
        if hasattr(n, "global_constraints") and not n.global_constraints.empty:
            scale = float(nh) / 8760.0
            if "constant" in n.global_constraints.columns:
                n.global_constraints["constant"] = n.global_constraints["constant"].astype(float) * scale
            logger.warning(
                "solve_opts nhours=%s: scaled GlobalConstraint.constant by %s (keep co2_limit active).",
                nh,
                scale,
            )
        # Also undo the representative-year weighting used elsewhere; for a truncated
        # dispatch smoke-test we want constraints/costs to reflect the truncated horizon.
        try:
            n.snapshot_weightings[:] = 1.0
        except Exception:
            pass
    freeze_capacities_and_zero_capex(n, zero_capital_cost=True)
    carriers_cfg = dispatch_cfg.get("carriers") or {}
    apply_segmented_carriers(n, carriers_cfg)
    if dispatch_cfg.get("zero_gas_fuel_marginal_cost", True) and "OCGT gas" in (carriers_cfg.get("Link") or {}):
        zero_gas_fuel_marginal_cost(n)
    opts_list = [o for o in str(opts).split("-") if o]
    solve_dispatch_lp(n, config, solving, opts_list)
    _log_ac_balance(n, "post-solve (in-memory)")
    os.makedirs(os.path.dirname(network_out), exist_ok=True)
    if hasattr(n, "links_t") and hasattr(n.links_t, "p2") and n.links_t.p2 is not None:
        n.links_t.p2 = n.links_t.p2.astype(float)
    if hasattr(n, "links_t") and hasattr(n.links_t, "p3"):
        n.links_t.p3 = n.links_t.p3.apply(pd.to_numeric, errors="coerce").fillna(0.0).infer_objects(copy=False)
    _log_ac_balance(n, "pre-export (in-memory)")
    n.export_to_netcdf(network_out)
    if dispatch_cfg.get("check_export_ac_balance", True):
        try:
            n_chk = pypsa.Network(network_out)
            _log_ac_balance(n_chk, "post-export (reloaded)")
        except Exception as e:  # pragma: no cover - diagnostics only
            logger.warning("Post-export AC balance reload check failed: %s", e)


if __name__ == "__main__":
    if "snakemake" not in globals():
        from _helpers import mock_snakemake

        os.chdir(_THIS_DIR)
        snakemake = mock_snakemake(
            "run_dispatch_segmented",
            opts="ll",
            topology="current+FCG",
            pathway="linear2050",
            co2_reduction="0.0",
            planning_horizons="2030",
            heating_demand="positive",
        )

    configure_logging(snakemake)
    cfg_root = snakemake.config
    dseg = cfg_root.get("dispatch_segmented_prices") or {}
    solve_opts = cfg_root.get("solving", {}).get("options", {})
    overrides = getattr(snakemake.input, "overrides", None)
    run(
        network_in=snakemake.input.network,
        network_out=snakemake.output.network,
        config=cfg_root,
        solving=snakemake.params.solving,
        opts=snakemake.wildcards.opts,
        solve_opts=solve_opts,
        using_single_node=snakemake.params.using_single_node,
        single_node_province=snakemake.params.single_node_province,
        overrides_path=str(overrides) if overrides else None,
        dispatch_cfg=dseg,
    )
