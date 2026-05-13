"""One-off diagnostics: compare thermal vs VRE/load on Oct 29–31."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pypsa

_DEFAULT_NC = (
    Path(__file__).resolve().parent.parent
    / "results/version-0512.1H.1/dispatch_segmented/positive/postnetwork-dispatch-seg-ll-current+FCG-linear2050-2025.nc"
)


def thermal_gen_mean_mw(n: pypsa.Network, snaps: list) -> tuple[float, dict[str, float]]:
    gens = n.generators

    def is_th(car: str) -> bool:
        s = car.lower()
        if "fuel" in s:
            return False
        return any(k in s for k in ("coal cc", "coal power plant")) or ("chp" in s) or ("ocgt" in s)

    total = 0.0
    det: dict[str, float] = {}
    if not snaps or not hasattr(n, "generators_t") or not hasattr(n.generators_t, "p"):
        return total, det
    p_all = n.generators_t.p.reindex(index=snaps)
    for c in gens.carrier.unique():
        if not is_th(str(c)):
            continue
        gn = list(gens.index[gens.carrier.astype(str) == str(c)])
        if not gn:
            continue
        p = (
            p_all[gn]
            .apply(lambda s: pd.to_numeric(s, errors="coerce"))
            .infer_objects(copy=False)
            .fillna(0.0)
            .astype(float)
        )
        mn = float(p.mean().mean())
        det[str(c)] = mn
        total += mn
    return total, det


def thermal_link_inj_mean_mw(n: pypsa.Network, snaps: list) -> tuple[float, dict[str, float]]:
    if not snaps or not hasattr(n, "links_t") or not hasattr(n.links_t, "p1"):
        return 0.0, {}
    lk = n.links
    p1 = n.links_t.p1.reindex(index=snaps)
    total = 0.0
    det: dict[str, float] = {}
    for c in lk.carrier.unique():
        s = str(c).lower()
        if not (("ocgt" in s) or ("chp" in s)):
            continue
        ln = list(lk.index[lk.carrier.astype(str) == str(c)])
        if not ln:
            continue
        inj = (
            (-p1[ln].apply(lambda s: pd.to_numeric(s, errors="coerce")).infer_objects(copy=False).fillna(0.0).astype(float))
            .clip(lower=0.0)
        )
        mn = float(inj.mean().mean())
        det[str(c)] = mn
        total += mn
    return total, det


def carrier_gen_mean(n: pypsa.Network, snaps: list, carriers: list[str]) -> dict[str, float]:
    gens = n.generators
    out = {}
    if not snaps or not hasattr(n, "generators_t"):
        return out
    p_all = n.generators_t.p.reindex(index=snaps)
    for k in carriers:
        gn = list(gens.index[gens.carrier.astype(str) == k])
        if not gn:
            continue
        p = (
            p_all[gn]
            .apply(lambda s: pd.to_numeric(s, errors="coerce"))
            .infer_objects(copy=False)
            .fillna(0.0)
            .astype(float)
        )
        out[k] = float(p.mean().mean())
    return out


def loads_mean(n: pypsa.Network, snaps: list) -> float | None:
    if not snaps or not hasattr(n, "loads_t") or not hasattr(n.loads_t, "p_set"):
        return None
    ps = (
        n.loads_t.p_set.reindex(index=snaps)
        .apply(lambda s: pd.to_numeric(s, errors="coerce"))
        .infer_objects(copy=False)
        .fillna(0.0)
        .astype(float)
    )
    return float(ps.mean().mean())


def main() -> None:
    nc = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_NC
    n = pypsa.Network(str(nc))
    idx = pd.DatetimeIndex(pd.to_datetime(pd.Index(n.snapshots)))

    dates = ["2025-10-29", "2025-10-30", "2025-10-31", "2025-10-15"]
    for ds in dates:
        d = pd.Timestamp(ds)
        snaps = idx[idx.normalize() == d.normalize()].tolist()
        if len(snaps) != 24:
            print(ds, "n_snapshots=", len(snaps))
            continue
        tg, d_g = thermal_gen_mean_mw(n, snaps)
        tl, d_l = thermal_link_inj_mean_mw(n, snaps)
        ld = loads_mean(n, snaps)
        v = carrier_gen_mean(n, snaps, ["onwind", "offwind", "solar"])
        print(f"\n=== {ds} ===")
        print(f"thermal gen (sum carrier means) MW: {tg:.1f}")
        print(f"thermal link inj MW: {tl:.1f}")
        print(f"load p_set mean MW: {ld}")
        print("VRE means:", ", ".join(f"{k}={v.get(k):.1f}" for k in sorted(v)))

    # Hourly extremes on Oct 30
    d30 = pd.Timestamp("2025-10-30")
    snaps30 = idx[idx.normalize() == d30.normalize()].tolist()
    gens = n.generators
    p = n.generators_t.p.reindex(index=snaps30)

    def sum_carrier(name: str) -> pd.Series:
        gn = list(gens.index[gens.carrier.astype(str) == name])
        if not gn:
            return pd.Series(0.0, index=snaps30)
        return (
            p[gn]
            .apply(lambda s: pd.to_numeric(s, errors="coerce"))
            .infer_objects(copy=False)
            .fillna(0.0)
            .astype(float)
            .sum(axis=1)
        )

    ow = sum_carrier("onwind")
    so = sum_carrier("solar")

    th_g = pd.Series(0.0, index=snaps30)
    for _c in gens.carrier.unique():
        s = str(_c).lower()
        if "fuel" in s:
            continue
        if ("coal cc" in s) or ("coal power plant" in s) or ("ocgt" in s and "fuel" not in s) or ("chp" in s):
            gn = list(gens.index[gens.carrier.astype(str) == str(_c)])
            sub = (
                p[gn]
                .apply(lambda s: pd.to_numeric(s, errors="coerce"))
                .infer_objects(copy=False)
                .fillna(0.0)
                .astype(float)
            )
            th_g = th_g.add(sub.sum(axis=1), fill_value=0.0)

    th_link = pd.Series(0.0, index=snaps30)
    if hasattr(n, "links") and hasattr(n.links_t, "p1"):
        lk = n.links
        for _c in lk.carrier.unique():
            s = str(_c).lower()
            if "ocgt" not in s and "chp" not in s:
                continue
            ln = list(lk.index[lk.carrier.astype(str) == str(_c)])
            th_link = th_link.add(
                n.links_t.p1.reindex(index=snaps30)[ln]
                .apply(lambda s: pd.to_numeric(s, errors="coerce"))
                .infer_objects(copy=False)
                .fillna(0.0)
                .astype(float)
                .mul(-1)
                .clip(lower=0.0)
                .sum(axis=1),
                fill_value=0.0,
            )

    th_all = th_g + th_link

    print("\n=== 2025-10-30 hourly (sums) ===")
    print(f"thermal gen hourly mean {float(th_g.mean()):.0f} MW, min {float(th_g.min()):.0f}, max {float(th_g.max()):.0f}")
    print(f"thermal links hourly mean {float(th_link.mean()):.0f} MW")
    print(f"thermal total hourly mean {float(th_all.mean()):.0f} MW")
    print(f"onwind sum hourly mean {float(ow.mean()):.0f} min={float(ow.min()):.0f} max={float(ow.max()):.0f}")
    print(f"solar sum hourly mean {float(so.mean()):.0f} min={float(so.min()):.0f} max={float(so.max()):.0f}")
    lm = loads_mean(n, snaps30)
    print(f"load p_set hourly mean MW (approx) {lm:.1f}")


if __name__ == "__main__":
    main()
