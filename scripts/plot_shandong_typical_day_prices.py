"""
Plot day-based electricity prices for Shandong from a solved PyPSA network.

This script:
- loads a solved `.nc` network
- reconstructs market prices via `reconstruct_market_prices`
- extracts the Shandong province electricity bus
- selects an actual day (not an average) and plots its 24h curve:
  - default: for each season, pick the most representative day (closest to that season's mean 24h curve)
  - optional: pick a specific calendar day via --date YYYY-MM-DD
- saves a PNG (and optionally CSV)

Usage (recommended):
  conda run -n pypsa-china python scripts/plot_shandong_typical_day_prices.py \
    --network results/.../postnetwork-....nc \
    --out results/.../shandong_typical_day_prices.png
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import matplotlib.pyplot as plt
import pypsa

# When running as `python scripts/foo.py`, ensure repo root is on sys.path so we
# can import sibling modules under `scripts/`.
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.reconstruct_market_prices import reconstruct_market_prices, ReconstructPriceConfig  # noqa: E402


def _season_of_month(month: int) -> str:
    # Northern-hemisphere seasons
    if month in (12, 1, 2):
        return "winter"
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    return "autumn"


def mean_seasonal_profile(series: pd.Series) -> pd.DataFrame:
    """
    Return seasonal mean 24h profiles (used only as a reference for day selection).

    Input:
      series: hourly time series (DatetimeIndex) of prices for one bus.

    Output:
      DataFrame index=hour (0..23), columns=[winter,spring,summer,autumn]
    """
    s = series.copy()
    if not isinstance(s.index, (pd.DatetimeIndex,)):
        s.index = pd.to_datetime(s.index)

    df = s.to_frame("price")
    df["season"] = df.index.month.map(_season_of_month)
    df["hour"] = df.index.hour
    prof = df.groupby(["season", "hour"])["price"].mean().unstack("season")

    # Ensure stable order
    for col in ["winter", "spring", "summer", "autumn"]:
        if col not in prof.columns:
            prof[col] = np.nan
    prof = prof[["winter", "spring", "summer", "autumn"]].sort_index()
    return prof


def _daily_matrix(series: pd.Series) -> pd.DataFrame:
    """
    Pivot an hourly time series into a daily matrix.

    Returns a DataFrame with:
    - index: date (datetime.date)
    - columns: hour (0..23)
    - values: price
    """
    s = series.copy()
    if not isinstance(s.index, (pd.DatetimeIndex,)):
        s.index = pd.to_datetime(s.index)

    df = s.to_frame("price")
    df["date"] = df.index.date
    df["hour"] = df.index.hour
    mat = df.pivot_table(index="date", columns="hour", values="price", aggfunc="mean")
    # Ensure all hours exist
    mat = mat.reindex(columns=list(range(24)))
    return mat


def representative_days_by_season(series: pd.Series) -> tuple[pd.DataFrame, dict[str, str]]:
    """
    Pick one actual day per season whose 24h curve is closest to that season's mean curve.

    Returns:
      (profiles, chosen_dates)
      - profiles: DataFrame index=hour, columns=seasons, values=price
      - chosen_dates: dict season -> YYYY-MM-DD string
    """
    s = series.copy()
    if not isinstance(s.index, (pd.DatetimeIndex,)):
        s.index = pd.to_datetime(s.index)

    daily = _daily_matrix(s)
    day_index = pd.to_datetime(pd.Index(daily.index.astype(str)))
    seasons = day_index.month.map(_season_of_month)

    mean_prof = mean_seasonal_profile(s)

    profiles = pd.DataFrame(index=pd.Index(range(24), name="hour"))
    chosen: dict[str, str] = {}

    for season in ["winter", "spring", "summer", "autumn"]:
        mask = seasons == season
        if not np.any(mask):
            profiles[season] = np.nan
            continue

        d = daily.loc[np.array(daily.index)[mask]]
        ref = mean_prof[season].to_numpy(dtype=float)
        # L2 distance ignoring NaNs
        x = d.to_numpy(dtype=float)
        diff = x - ref[None, :]
        diff = np.where(np.isfinite(diff), diff, 0.0)
        dist = np.sqrt((diff * diff).sum(axis=1))
        i = int(np.argmin(dist))
        date_str = str(d.index[i])
        chosen[season] = date_str
        profiles[season] = d.iloc[i].to_numpy(dtype=float)

    return profiles, chosen


def peak_days_by_season(series: pd.Series, *, agg: str = "max") -> tuple[pd.DataFrame, dict[str, str]]:
    """
    Pick one actual day per season with the highest price level.

    Parameters
    ----------
    agg:
      - 'max': pick the day with the highest hourly max (peak price)
      - 'mean': pick the day with the highest daily mean
    """
    s = series.copy()
    if not isinstance(s.index, (pd.DatetimeIndex,)):
        s.index = pd.to_datetime(s.index)

    daily = _daily_matrix(s)
    day_index = pd.to_datetime(pd.Index(daily.index.astype(str)))
    seasons = day_index.month.map(_season_of_month)

    profiles = pd.DataFrame(index=pd.Index(range(24), name="hour"))
    chosen: dict[str, str] = {}

    for season in ["winter", "spring", "summer", "autumn"]:
        mask = seasons == season
        if not np.any(mask):
            profiles[season] = np.nan
            continue

        d = daily.loc[np.array(daily.index)[mask]]
        if agg == "mean":
            score = d.mean(axis=1)
        else:
            score = d.max(axis=1)
        i = int(np.nanargmax(score.to_numpy(dtype=float)))
        date_str = str(d.index[i])
        chosen[season] = date_str
        profiles[season] = d.iloc[i].to_numpy(dtype=float)

    return profiles, chosen


def profile_for_specific_day(series: pd.Series, date_str: str) -> pd.Series:
    """
    Extract a single day's 24h profile from an hourly series.
    """
    s = series.copy()
    if not isinstance(s.index, (pd.DatetimeIndex,)):
        s.index = pd.to_datetime(s.index)

    date = pd.to_datetime(date_str).date()
    day = s.loc[s.index.date == date]
    if day.empty:
        raise ValueError(f"No data found for date={date_str}")

    mat = _daily_matrix(day)
    prof = mat.iloc[0]
    prof.index.name = "hour"
    return prof


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--network", required=True, help="Path to solved network .nc")
    ap.add_argument("--province", default="Shandong", help="Province bus name (default: Shandong)")
    ap.add_argument("--out", required=True, help="Output PNG path")
    ap.add_argument("--out-csv", default=None, help="Optional output CSV path for hourly profiles")
    ap.add_argument("--price-tol", type=float, default=1e-3, help="Price-zone similarity tolerance")
    ap.add_argument(
        "--mode",
        choices=["representative", "peakmax", "peakmean"],
        default="representative",
        help="Day selection mode when --date is not set.",
    )
    ap.add_argument(
        "--date",
        default=None,
        help="Plot a specific day (YYYY-MM-DD). If omitted, one representative day per season is selected.",
    )
    args = ap.parse_args()

    n = pypsa.Network(args.network)

    cfg = ReconstructPriceConfig(price_tol=float(args.price_tol))
    prices = reconstruct_market_prices(n, config=cfg)

    if args.province not in prices.columns:
        raise SystemExit(
            f"Province '{args.province}' not found in reconstructed electricity buses. "
            f"Available examples: {list(prices.columns[:10])} ..."
        )

    s = prices[args.province]

    chosen_dates: dict[str, str] | None = None
    if args.date is None:
        if args.mode == "representative":
            prof, chosen_dates = representative_days_by_season(s)
            title = f"Representative-day electricity price (reconstructed) — {args.province}"
        elif args.mode == "peakmean":
            prof, chosen_dates = peak_days_by_season(s, agg="mean")
            title = f"Peak-mean day electricity price (reconstructed) — {args.province}"
        else:
            prof, chosen_dates = peak_days_by_season(s, agg="max")
            title = f"Peak-max day electricity price (reconstructed) — {args.province}"
    else:
        one = profile_for_specific_day(s, args.date)
        prof = pd.DataFrame({"selected_day": one})
        title = f"Electricity price (reconstructed) — {args.province} — {args.date}"

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    plt.figure(figsize=(9, 4.5))
    x = prof.index.to_numpy()
    if args.date is None:
        for season, color in [
            ("winter", "#1f77b4"),
            ("spring", "#2ca02c"),
            ("summer", "#ff7f0e"),
            ("autumn", "#9467bd"),
        ]:
            y = prof[season].to_numpy(dtype=float)
            label = season
            if chosen_dates and season in chosen_dates:
                label = f"{season} ({chosen_dates[season]})"
            plt.plot(x, y, label=label, linewidth=2)
    else:
        plt.plot(x, prof.iloc[:, 0].to_numpy(dtype=float), label="selected_day", linewidth=2)

    plt.title(title)
    plt.xlabel("Hour of day")
    plt.ylabel("Price (CNY/MWh)")
    plt.xticks(np.arange(0, 24, 3))
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=180)

    if args.out_csv is not None:
        Path(args.out_csv).parent.mkdir(parents=True, exist_ok=True)
        prof.to_csv(args.out_csv, index_label="hour")


if __name__ == "__main__":
    main()

