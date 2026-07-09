#!/usr/bin/env python3
"""One-off: wind capacity vs guard upper limit for storage-x1 postnetworks."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import pypsa

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

VERSION_DIR = ROOT / "results" / "version-0621.1H.3-storage-x1"
LIMITS_CSV = ROOT / "data/p_nom/provincial_capacity_guard_limits.csv"
POST_DIR = VERSION_DIR / "postnetworks" / "positive"
STEM = "ll-current+FCG-linear2050"
AT_UPPER_TOL = 0.995


def main() -> None:
    limits = pd.read_csv(LIMITS_CSV)
    limits = limits[limits["carrier"].isin(["onwind", "offwind"])].copy()

    nat_rows: list[dict] = []
    prov_rows: list[dict] = []

    for nc in sorted(POST_DIR.glob(f"postnetwork-{STEM}-*.nc")):
        m = re.search(r"-(\d{4})\.nc$", nc.name)
        if not m:
            continue
        year = int(m.group(1))
        n = pypsa.Network(nc)
        gens = n.generators
        cap_col = "p_nom_opt" if "p_nom_opt" in gens.columns else "p_nom"

        for carrier in ("onwind", "offwind"):
            g = gens[gens.carrier == carrier]
            if g.empty:
                continue
            built_by_bus = g.groupby("bus")[cap_col].sum()
            lim_y = limits[(limits["year"] == year) & (limits["carrier"] == carrier)].set_index("province")

            for prov, mw in built_by_bus.items():
                prov = str(prov)
                if prov not in lim_y.index:
                    continue
                upper = float(lim_y.at[prov, "upper_limit_mw_before_existing_stock_adjustment"])
                target = float(lim_y.at[prov, "target_mw_before_existing_stock_adjustment"])
                if upper <= 0 and target <= 0:
                    continue
                prov_rows.append(
                    {
                        "year": year,
                        "carrier": carrier,
                        "province": prov,
                        "built_mw": float(mw),
                        "target_mw": target,
                        "upper_mw": upper,
                        "built_over_upper_pct": 100 * float(mw) / upper if upper > 0 else float("nan"),
                        "at_upper_cap": bool(float(mw) >= AT_UPPER_TOL * upper) if upper > 0 else False,
                    }
                )

            built_nat = float(g[cap_col].sum())
            upper_nat = float(lim_y["upper_limit_mw_before_existing_stock_adjustment"].sum())
            target_nat = float(lim_y["target_mw_before_existing_stock_adjustment"].sum())
            nat_rows.append(
                {
                    "year": year,
                    "carrier": carrier,
                    "built_mw": built_nat,
                    "target_mw": target_nat,
                    "upper_mw": upper_nat,
                    "built_over_target_pct": 100 * built_nat / target_nat if target_nat > 0 else float("nan"),
                    "built_over_upper_pct": 100 * built_nat / upper_nat if upper_nat > 0 else float("nan"),
                    "at_upper_national": bool(built_nat >= AT_UPPER_TOL * upper_nat) if upper_nat > 0 else False,
                }
            )

    nat = pd.DataFrame(nat_rows)
    prov = pd.DataFrame(prov_rows)

    comb = nat.groupby("year", as_index=False)[["built_mw", "target_mw", "upper_mw"]].sum()
    comb["carrier"] = "wind_total"
    comb["built_over_target_pct"] = 100 * comb["built_mw"] / comb["target_mw"]
    comb["built_over_upper_pct"] = 100 * comb["built_mw"] / comb["upper_mw"]
    comb["at_upper_national"] = comb["built_mw"] >= AT_UPPER_TOL * comb["upper_mw"]

    cap_share = (
        prov[prov["upper_mw"] > 0]
        .groupby(["year", "carrier"], as_index=False)
        .agg(
            n_provinces=("province", "count"),
            n_at_upper=("at_upper_cap", "sum"),
            mean_built_over_upper_pct=("built_over_upper_pct", "mean"),
        )
    )
    cap_share["pct_provinces_at_upper"] = 100 * cap_share["n_at_upper"] / cap_share["n_provinces"]

    nat.sort_values(["year", "carrier"]).to_csv(
        VERSION_DIR / "wind_capacity_guard_utilization_national.csv", index=False
    )
    comb.sort_values("year").to_csv(VERSION_DIR / "wind_capacity_guard_utilization_combined.csv", index=False)
    cap_share.sort_values(["year", "carrier"]).to_csv(
        VERSION_DIR / "wind_capacity_guard_provincial_at_upper_share.csv", index=False
    )

    print("=== 全国风电装机 / guard 上限 (130% 目标) ===")
    print(comb.to_string(index=False, formatters={"built_mw": "{:,.0f}".format, "upper_mw": "{:,.0f}".format}))
    print("\n=== 分省触顶比例 (% 省份装机 >= 99.5% 上限) ===")
    print(cap_share.pivot(index="year", columns="carrier", values="pct_provinces_at_upper").round(1).to_string())
    print("\n=== 全国装机/上限 (%) ===")
    print(nat.pivot(index="year", columns="carrier", values="built_over_upper_pct").round(1).to_string())


if __name__ == "__main__":
    main()
