from __future__ import annotations

import calendar
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TimeSample:
    snapshots: pd.DatetimeIndex
    objective_weightings: pd.Series
    generator_weightings: pd.Series
    store_weightings: pd.Series

    def source_snapshots(self, year: int) -> pd.DatetimeIndex:
        return self.snapshots.map(lambda t: t.replace(year=year))


def _freq_hours(freq: str) -> float:
    delta = pd.to_timedelta(freq)
    return float(delta / pd.Timedelta(hours=1))


def _annual_snapshots(planning_year: int, freq: str) -> pd.DatetimeIndex:
    if planning_year % 4 != 0:
        return pd.date_range(
            f"{planning_year}-01-01 00:00",
            f"{planning_year}-12-31 23:00",
            freq=freq,
        )
    snapshots = pd.date_range("2025-01-01 00:00", "2025-12-31 23:00", freq=freq)
    return snapshots.map(lambda t: t.replace(year=planning_year))


def _day_snapshots(year: int, month: int, day: int, freq: str) -> pd.DatetimeIndex:
    day = min(day, calendar.monthrange(year, month)[1])
    start = pd.Timestamp(year=year, month=month, day=day, hour=0)
    end = start + pd.Timedelta(days=1) - pd.to_timedelta(freq)
    return pd.date_range(start, end, freq=freq)


def _month_groups(n_days: int) -> list[list[int]]:
    if not 1 <= n_days <= 12:
        raise ValueError("time_sampling.n_days must be between 1 and 12")
    months = list(range(1, 13))
    base, extra = divmod(12, n_days)
    groups = []
    offset = 0
    for group_idx in range(n_days):
        size = base + (1 if group_idx < extra else 0)
        groups.append(months[offset : offset + size])
        offset += size
    return groups


def _representative_months(groups: list[list[int]], configured_months: list[int] | None) -> list[int]:
    if configured_months:
        if len(configured_months) != len(groups):
            raise ValueError("time_sampling.months length must match time_sampling.n_days")
        months = [int(m) for m in configured_months]
        if any(m < 1 or m > 12 for m in months):
            raise ValueError("time_sampling.months values must be in 1..12")
        return months
    return [group[len(group) // 2] for group in groups]


def _sample_days(config: dict, planning_year: int, freq: str) -> TimeSample:
    n_days = int(config.get("n_days", 12))
    day_of_month = int(config.get("day_of_month", 15))
    groups = _month_groups(n_days)
    months = _representative_months(groups, config.get("months"))
    freq_h = _freq_hours(freq)

    chunks = []
    weights = []
    for month, represented_months in zip(months, groups):
        snapshots = _day_snapshots(planning_year, month, day_of_month, freq)
        represented_days = sum(calendar.monthrange(planning_year, m)[1] for m in represented_months)
        chunks.append(snapshots)
        weights.append(pd.Series(represented_days * freq_h, index=snapshots))

    model_snapshots = pd.DatetimeIndex([ts for chunk in chunks for ts in chunk])
    objective = pd.concat(weights).reindex(model_snapshots)
    stores = pd.Series(freq_h, index=model_snapshots)
    return TimeSample(model_snapshots, objective, objective.copy(), stores)


def _manual_sample(config: dict, planning_year: int, freq: str) -> TimeSample:
    freq_h = _freq_hours(freq)
    chunks = []

    for date in config.get("dates", []) or []:
        ts = pd.Timestamp(date)
        chunks.append(_day_snapshots(planning_year, ts.month, ts.day, freq))

    for start, end in config.get("ranges", []) or []:
        start_ts = pd.Timestamp(start).replace(year=planning_year)
        end_ts = pd.Timestamp(end).replace(year=planning_year)
        chunks.append(pd.date_range(start_ts, end_ts, freq=freq))

    if not chunks:
        raise ValueError("time_sampling.manual requires at least one date or range")

    snapshots = pd.DatetimeIndex([ts for chunk in chunks for ts in chunk]).drop_duplicates()
    annual_hours = 8760.0
    representative_weight = annual_hours / len(snapshots)
    objective = pd.Series(representative_weight, index=snapshots)
    stores = pd.Series(freq_h, index=snapshots)
    return TimeSample(snapshots, objective, objective.copy(), stores)


def build_time_sample(config: dict, planning_horizon: str | int) -> TimeSample:
    planning_year = int(planning_horizon)
    freq = str(config.get("freq", "1h"))
    sampling = config.get("time_sampling") or {}
    enabled = bool(sampling.get("enabled", False))
    mode = str(sampling.get("mode", "full"))

    if not enabled or mode == "full":
        snapshots = _annual_snapshots(planning_year, freq)
        weights = pd.Series(_freq_hours(freq), index=snapshots)
        return TimeSample(snapshots, weights, weights.copy(), weights.copy())

    if mode in {"sample_days", "monthly_typical_days"}:
        return _sample_days(sampling, planning_year, freq)
    if mode == "manual":
        return _manual_sample(sampling.get("manual", sampling), planning_year, freq)

    raise ValueError(f"Unsupported time_sampling.mode: {mode}")
