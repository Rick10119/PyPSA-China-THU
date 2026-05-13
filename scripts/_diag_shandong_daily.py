"""Summarize Shandong load / VRE / thermal for selected days (dispatch-seg .nc)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pypsa

_DEFAULT_NC = (
    Path(__file__).resolve().parent.parent
    / "results/version-0512.1H.1/dispatch_segmented/positive/postnetwork-dispatch-seg-ll-current+FCG-linear2050-2025.nc"
)


def bus_prov(bus: str) -> str:
    return str(bus).split(" ", 1)[0]


def is_thermal_gen_row(row: pd.Series) -> bool:
    c = str(row.get("carrier", "")).lower()
    if "fuel" in c:
        return False
    return ("coal cc" in c) or ("coal power plant" in c) or ("ocgt" in c and "fuel" not in c) or ("chp" in c)


def is_thermal_link_row(row: pd.Series) -> bool:
    c = str(row.get("carrier", "")).lower()
    return ("ocgt" in c) or ("chp" in c)


def main() -> None:
    prov = str(sys.argv[2]) if len(sys.argv) > 2 else "Shandong"
    nc = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_NC
    n = pypsa.Network(str(nc))
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Index(n.snapshots)))

    def sum_gen_thermal(snaps: list) -> pd.Series:
        g = n.generators
        mask = g["bus"].astype(str).map(lambda b: bus_prov(b) == prov) & g.apply(is_thermal_gen_row, axis=1)
        idxg = list(g.index[mask])
        if not idxg:
            return pd.Series(0.0, index=snaps)
        p = n.generators_t.p.reindex(index=snaps)[idxg]
        p = p.apply(lambda s: pd.to_numeric(s, errors="coerce")).fillna(0.0).astype(float)
        return p.sum(axis=1)

    def sum_link_thermal(snaps: list) -> pd.Series:
        lk = n.links
        mask = lk["bus1"].astype(str).map(lambda b: bus_prov(b) == prov) & lk.apply(is_thermal_link_row, axis=1)
        idxl = list(lk.index[mask])
        if not idxl:
            return pd.Series(0.0, index=snaps)
        p1 = n.links_t.p1.reindex(index=snaps)[idxl]
        p1 = p1.apply(lambda s: pd.to_numeric(s, errors="coerce")).fillna(0.0).astype(float)
        return (-p1).clip(lower=0.0).sum(axis=1)

    def sum_carrier(snaps: list, carrier: str) -> pd.Series:
        g = n.generators
        idxg = [i for i in g.index[g.carrier.astype(str) == carrier] if bus_prov(g.at[i, "bus"]) == prov]
        if not idxg:
            return pd.Series(0.0, index=snaps)
        p = n.generators_t.p.reindex(index=snaps)[idxg]
        p = p.apply(lambda s: pd.to_numeric(s, errors="coerce")).fillna(0.0).astype(float)
        return p.sum(axis=1)

    def sum_load(snaps: list) -> pd.Series:
        ld = n.loads
        idxl = list(ld.index[ld["bus"].astype(str).map(lambda b: bus_prov(b) == prov)])
        if not idxl:
            return pd.Series(0.0, index=snaps)
        ps = n.loads_t.p_set.reindex(index=snaps)[idxl]
        return ps.apply(lambda s: pd.to_numeric(s, errors="coerce")).fillna(0.0).astype(float).sum(axis=1)

    days = ["2025-10-29", "2025-10-30", "2025-10-15", "2025-10-31"]
    print(f"network: {nc}")
    print(f"province: {prov}")
    for ds in days:
        d = pd.Timestamp(ds)
        snaps = idx[idx.normalize() == d.normalize()].tolist()
        if len(snaps) != 24:
            print(f"{ds}: snapshots={len(snaps)}")
            continue
        th_g = sum_gen_thermal(snaps)
        th_l = sum_link_thermal(snaps)
        th = th_g + th_l
        ld = sum_load(snaps)
        print(f"\n{ds}")
        print(f"  load MW mean/min/max: {ld.mean():.0f} / {ld.min():.0f} / {ld.max():.0f}")
        print(
            f"  thermal MW mean/min/max: {th.mean():.0f} / {th.min():.0f} / {th.max():.0f} "
            f"(gen {th_g.mean():.0f}, link_inj {th_l.mean():.0f})"
        )
        print(
            f"  VRE MW mean: onwind {sum_carrier(snaps, 'onwind').mean():.0f}, "
            f"solar {sum_carrier(snaps, 'solar').mean():.0f}, "
            f"offwind {sum_carrier(snaps, 'offwind').mean():.0f}"
        )


if __name__ == "__main__":
    main()
