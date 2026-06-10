# SPDX-FileCopyrightText: 2026 Ruike Lyu
#
# SPDX-License-Identifier: MIT
"""Apply a two-stage Shandong thermal minimum-output adjustment.

Stage 1 is the solved dispatch network. Stage 2 checks whether configured
synchronous/thermal output is below the minimum-output requirement. If it is,
wind/PV dispatch is reduced proportionally to original VRE dispatch and thermal
output is increased by the same amount.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

import pandas as pd
import pypsa
import yaml


DEFAULT_CONFIG = Path("configs/generated_shandong_negative_price_0609.1H.1.yaml")
DEFAULT_OUT = Path("data/shandong_negative_price/two_stage_min_output_adjustment.csv")


def _bus_province(bus: str) -> str:
    return str(bus).split(" ", 1)[0]


def _is_ac_bus(n: pypsa.Network, bus: str, province: str) -> bool:
    b = str(bus)
    if _bus_province(b) != str(province):
        return False
    if b not in n.buses.index:
        return False
    if "carrier" in n.buses.columns:
        return str(n.buses.at[b, "carrier"]) == "AC"
    return b == str(province)


def _load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _two_stage_cfg(config: dict) -> dict:
    study = config.get("shandong_negative_price_study") or {}
    return study.get("two_stage_thermal_min_output_adjustment") or {}


def _selected_link(n: pypsa.Network, row: pd.Series, link_cfg: dict) -> bool:
    carrier = str(row.get("carrier", ""))
    if carrier not in link_cfg:
        return False
    spec = link_cfg.get(carrier) or {}
    if not isinstance(spec, dict):
        return True
    only_bus1 = str(spec.get("only_bus1_carrier", "") or "")
    if not only_bus1:
        return True
    bus1 = str(row.get("bus1", ""))
    return bus1 in n.buses.index and str(n.buses.at[bus1, "carrier"]) == only_bus1


def _sum_load(n: pypsa.Network, province: str, snapshots: pd.Index) -> pd.Series:
    out = pd.Series(0.0, index=snapshots, dtype=float)
    loads = n.loads.index[n.loads["bus"].astype(str).map(lambda b: _is_ac_bus(n, b, province))]
    if len(loads) == 0:
        return out
    p_set = n.loads_t.p_set.reindex(index=snapshots, columns=loads).fillna(0.0)
    return p_set.apply(pd.to_numeric, errors="coerce").fillna(0.0).astype(float).sum(axis=1)


def _sum_generators(
    n: pypsa.Network,
    province: str,
    snapshots: pd.Index,
    carriers: Iterable[str],
) -> pd.DataFrame:
    carriers_set = set(map(str, carriers))
    out = pd.DataFrame(index=snapshots)
    if not hasattr(n, "generators_t") or not hasattr(n.generators_t, "p"):
        return out
    gen = n.generators
    p = n.generators_t.p.reindex(index=snapshots).fillna(0.0)
    for carrier in sorted(carriers_set):
        idx = gen.index[
            (gen["carrier"].astype(str) == carrier)
            & gen["bus"].astype(str).map(lambda b: _is_ac_bus(n, b, province))
        ]
        if len(idx) == 0:
            out[carrier] = 0.0
        else:
            out[carrier] = (
                p.reindex(columns=idx)
                .apply(pd.to_numeric, errors="coerce")
                .fillna(0.0)
                .astype(float)
                .clip(lower=0.0)
                .sum(axis=1)
            )
    return out.fillna(0.0)


def _sum_thermal(
    n: pypsa.Network,
    province: str,
    snapshots: pd.Index,
    cfg: dict,
) -> pd.Series:
    out = pd.Series(0.0, index=snapshots, dtype=float)
    thermal_cfg = cfg.get("thermal_carriers") or {}
    gen_carriers = set(map(str, thermal_cfg.get("Generator") or []))
    link_cfg = thermal_cfg.get("Link") or {}

    if gen_carriers:
        gen = n.generators
        idx = gen.index[
            gen["carrier"].astype(str).isin(gen_carriers)
            & gen["bus"].astype(str).map(lambda b: _is_ac_bus(n, b, province))
        ]
        if len(idx):
            p = n.generators_t.p.reindex(index=snapshots, columns=idx).fillna(0.0)
            out = out.add(
                p.apply(pd.to_numeric, errors="coerce").fillna(0.0).astype(float).clip(lower=0.0).sum(axis=1),
                fill_value=0.0,
            )

    if link_cfg and hasattr(n, "links_t") and hasattr(n.links_t, "p1"):
        links = n.links
        idx = [
            l
            for l, row in links.iterrows()
            if _selected_link(n, row, link_cfg) and _is_ac_bus(n, str(row.get("bus1", "")), province)
        ]
        if idx:
            p1 = n.links_t.p1.reindex(index=snapshots, columns=idx).fillna(0.0)
            out = out.add(
                (-p1.apply(pd.to_numeric, errors="coerce").fillna(0.0).astype(float)).clip(lower=0.0).sum(axis=1),
                fill_value=0.0,
            )

    return out.fillna(0.0)


def build_adjustment(network: str | Path, config: dict, province: str | None = None) -> pd.DataFrame:
    n = pypsa.Network(str(network))
    cfg = _two_stage_cfg(config)
    if not cfg.get("enabled", True):
        raise ValueError("two_stage_thermal_min_output_adjustment is disabled.")
    province = str(province or cfg.get("province") or config.get("single_node_province") or "Shandong")
    snapshots = pd.Index(n.snapshots)

    ratio = float(cfg.get("min_output_ratio") or (config.get("synchronous_generation_floor") or {}).get("ratio", 0.10))
    vre_carriers = list(map(str, cfg.get("vre_carriers") or ["onwind", "offwind", "solar"]))

    load = _sum_load(n, province, snapshots)
    thermal = _sum_thermal(n, province, snapshots, cfg)
    floor = (load * ratio).clip(lower=0.0)
    deficit = (floor - thermal).clip(lower=0.0)

    vre = _sum_generators(n, province, snapshots, vre_carriers)
    vre_total = vre.sum(axis=1)
    curtail_total = pd.concat([deficit, vre_total], axis=1).min(axis=1)
    shares = vre.divide(vre_total.where(vre_total > 0.0), axis=0).fillna(0.0)
    curtail = shares.mul(curtail_total, axis=0)
    adjusted_vre = (vre - curtail).clip(lower=0.0)
    adjusted_thermal = thermal + curtail_total

    out = pd.DataFrame(
        {
            "snapshot": snapshots,
            "province": province,
            "load_mw": load.to_numpy(dtype=float),
            "min_output_floor_mw": floor.to_numpy(dtype=float),
            "thermal_stage1_mw": thermal.to_numpy(dtype=float),
            "min_output_deficit_mw": deficit.to_numpy(dtype=float),
            "min_output_binding": (deficit > 1e-6).to_numpy(dtype=bool),
            "vre_stage1_mw": vre_total.to_numpy(dtype=float),
            "vre_curtailment_total_mw": curtail_total.to_numpy(dtype=float),
            "thermal_stage2_mw": adjusted_thermal.to_numpy(dtype=float),
            "vre_stage2_mw": adjusted_vre.sum(axis=1).to_numpy(dtype=float),
        }
    )
    for carrier in vre_carriers:
        out[f"{carrier}_stage1_mw"] = vre.get(carrier, pd.Series(0.0, index=snapshots)).to_numpy(dtype=float)
        out[f"{carrier}_curtailment_mw"] = curtail.get(carrier, pd.Series(0.0, index=snapshots)).to_numpy(dtype=float)
        out[f"{carrier}_stage2_mw"] = adjusted_vre.get(carrier, pd.Series(0.0, index=snapshots)).to_numpy(dtype=float)
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--network", required=True, help="Stage-1 solved dispatch .nc file.")
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--province", default=None)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    config = _load_config(args.config)
    out = build_adjustment(args.network, config, province=args.province)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
