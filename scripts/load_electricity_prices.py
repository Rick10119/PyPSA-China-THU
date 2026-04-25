from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pandas as pd
import xarray as xr


@dataclass(frozen=True)
class ElectricityPriceSpec:
    """
    Specification for reading exogenous electricity prices.

    Supported input layouts:
    - long: columns include region/bus + hour/snapshot + price
    - wide: first column hour/snapshot, remaining columns are regions/buses
    """

    region_col: str = "province"
    bus_col: str = "bus"
    hour_col: str = "hour"
    snapshot_col: str = "snapshot"
    price_col: str = "price"
    sheet_name: str | int | None = None
    timezone: str | None = None


def _read_table(path: Path, spec: ElectricityPriceSpec) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path, sheet_name=spec.sheet_name)
    raise ValueError(f"Unsupported price file extension: {path.suffix}")


def _as_snapshots_from_hour(
    snapshots: pd.Index,
    hour: pd.Series,
) -> pd.DatetimeIndex:
    if len(snapshots) == 0:
        raise ValueError("snapshots is empty")

    # snapshots are expected to be hourly (or resampled), but for price mapping we
    # only need a stable index of the same length/order.
    hour = pd.to_numeric(hour, errors="raise").astype(int)
    if hour.min() < 1:
        raise ValueError("hour must be 1-indexed (1..N)")
    if hour.max() > len(snapshots):
        raise ValueError(f"hour exceeds snapshots length: hour_max={hour.max()} len(snapshots)={len(snapshots)}")

    # Map 1..N onto the provided snapshot index
    snap_dt = pd.DatetimeIndex(pd.to_datetime(snapshots))
    return snap_dt[hour.to_numpy() - 1]


def _downsample_hour_index_to_snapshots_len(
    *,
    df: pd.DataFrame,
    hour_col: str,
    snapshots_len: int,
    agg: str = "mean",
) -> pd.DataFrame:
    """
    Downsample hourly `hour_col` (1..H) to match `snapshots_len` snapshots.

    Example: if `hour_max` is 8760 and `snapshots_len` is 1460, infer slot=6 and
    aggregate hours into 6-hour blocks -> new hour 1..1460.
    """
    if snapshots_len <= 0:
        raise ValueError("snapshots_len must be positive")

    hour = pd.to_numeric(df[hour_col], errors="raise").astype(int)
    if hour.min() < 1:
        raise ValueError("hour must be 1-indexed (1..N)")

    hour_max = int(hour.max())
    if hour_max <= snapshots_len:
        return df

    # infer integer slot
    if hour_max % snapshots_len != 0:
        raise ValueError(
            f"hour exceeds snapshots length but is not divisible: hour_max={hour_max} len(snapshots)={snapshots_len}"
        )
    slot = hour_max // snapshots_len
    if slot <= 0:
        raise ValueError(f"invalid inferred slot={slot}")

    out = df.copy()
    out["_hour_block"] = ((hour - 1) // slot + 1).astype(int)  # 1..snapshots_len
    out = out.drop(columns=[hour_col]).rename(columns={"_hour_block": hour_col})
    return out


def load_electricity_prices(
    *,
    snapshots: pd.Index | None = None,
    path: str | Path,
    spec: ElectricityPriceSpec | None = None,
    index_mode: Literal["auto", "hour", "snapshot"] = "auto",
    region_mode: Literal["auto", "province", "bus"] = "auto",
    expected_regions: list[str] | None = None,
    require_complete_hours: bool = True,
    require_non_negative: bool = True,
    fallback_network_path: str | Path | None = None,
    export_if_missing: bool = True,
    export_format: Literal["long", "wide"] = "long",
    export_split_per_region: bool = False,
    export_write_combined: bool = True,
) -> pd.DataFrame:
    """
    Load exogenous electricity prices and return DataFrame shaped like
    `n.buses_t.marginal_price`: index=snapshots, columns=buses (or provinces),
    values=price.

    Parameters
    ----------
    snapshots:
        Target snapshot index to align to (typically `n.snapshots`).
        If None:
        - when reading from a fallback `.nc`, snapshots are taken from that network (typically 8760)
        - when reading from CSV/Excel, snapshots are inferred from the file (snapshot column) or from hour count
    path:
        CSV/Excel path.
    spec:
        Column naming spec.
    index_mode:
        - 'hour': use hour column (1..len(snapshots))
        - 'snapshot': use snapshot datetime column
        - 'auto': prefer snapshot column if present else hour column
    region_mode:
        - 'province': use province column as columns
        - 'bus': use bus column as columns
        - 'auto': prefer bus column if present else province column
    expected_regions:
        If provided, enforce that these regions exist as columns in the output.
    require_complete_hours:
        If true, require full coverage of all snapshots for each region (no gaps).
    require_non_negative:
        If true, reject negative prices.
    fallback_network_path:
        If `path` does not exist, read prices from this solved network netcdf (`.nc`)
        file (e.g. a `postnetwork-*.nc`) using `buses_t.marginal_price`.
    export_if_missing:
        If true and `path` does not exist, write a CSV to `path` after extracting
        prices from `fallback_network_path`.
    export_format:
        When exporting, write either a long table (province/bus, hour/snapshot, price)
        or a wide table (hour/snapshot + one column per province/bus).
    export_split_per_region:
        If true, export one CSV per region/bus (one column) instead of a single combined file.
    export_write_combined:
        When `export_split_per_region` is true, also write the combined CSV to `path`.
        This avoids re-extracting from the fallback network on every run.
    """

    spec = spec or ElectricityPriceSpec()
    path = Path(path)

    if not path.exists():
        if fallback_network_path is None:
            raise FileNotFoundError(
                f"Electricity price file not found: {path}. "
                "Provide `fallback_network_path` to extract prices from a solved .nc network."
            )
        fallback_network_path = Path(fallback_network_path)
        if not fallback_network_path.exists():
            raise FileNotFoundError(f"fallback_network_path not found: {fallback_network_path}")

        prices = extract_prices_from_network(
            network_path=fallback_network_path,
            snapshots=snapshots,
            expected_regions=expected_regions,
            require_non_negative=require_non_negative,
        )

        if export_if_missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            export_prices_to_csv(
                prices=prices,
                out_path=path,
                spec=spec,
                export_format=export_format,
                region_mode=region_mode,
                index_mode=index_mode,
                split_per_region=export_split_per_region,
                write_combined=export_write_combined,
            )

        return prices

    df = _read_table(path, spec)
    if df.empty:
        raise ValueError(f"Price file is empty: {path}")

    cols = set(map(str, df.columns))

    # Detect wide format: has hour/snapshot but no explicit price column
    has_price_col = spec.price_col in cols
    has_hour_col = spec.hour_col in cols
    has_snapshot_col = spec.snapshot_col in cols

    if index_mode == "auto":
        index_mode = "snapshot" if has_snapshot_col else "hour"

    if region_mode == "auto":
        region_mode = "bus" if spec.bus_col in cols else "province"

    if not has_price_col:
        # wide format
        idx_col = spec.snapshot_col if index_mode == "snapshot" else spec.hour_col
        if idx_col not in cols:
            raise ValueError(f"Wide format requires an index column '{idx_col}'")
        value_cols = [c for c in df.columns if str(c) != idx_col]
        if not value_cols:
            raise ValueError("Wide format must have at least one region/bus column")

        wide = df[[idx_col] + value_cols].copy()
        wide = wide.rename(columns={idx_col: "_idx"})
        if index_mode == "snapshot":
            snap = pd.to_datetime(wide["_idx"], errors="raise")
        else:
            if snapshots is not None:
                # If the file is hourly (e.g. 1..8760) but the network snapshots are coarser
                # (e.g. 6h -> len=1460), downsample by averaging within each slot block.
                hour = pd.to_numeric(wide["_idx"], errors="raise").astype(int)
                if hour.max() > len(snapshots):
                    wide2 = wide.copy()
                    wide2[spec.hour_col] = wide2["_idx"]
                    wide2 = _downsample_hour_index_to_snapshots_len(
                        df=wide2,
                        hour_col=spec.hour_col,
                        snapshots_len=len(snapshots),
                    )
                    # average prices within each hour-block
                    wide = wide2.groupby(spec.hour_col, as_index=False)[value_cols].mean(numeric_only=True)
                    wide = wide.rename(columns={spec.hour_col: "_idx"})

            if snapshots is None:
                # infer from hour count
                n_hours = int(pd.to_numeric(wide["_idx"], errors="raise").max())
                base = pd.Timestamp("2000-01-01 00:00")
                snapshots = pd.date_range(base, periods=n_hours, freq="1h")
            snap = _as_snapshots_from_hour(pd.Index(snapshots), wide["_idx"])
        wide = wide.drop(columns=["_idx"])
        wide.index = pd.DatetimeIndex(snap)
        out = wide
    else:
        # long format
        region_col = spec.bus_col if region_mode == "bus" else spec.region_col
        if region_col not in cols:
            raise ValueError(f"Long format requires region column '{region_col}'")

        if index_mode == "snapshot":
            if spec.snapshot_col not in cols:
                raise ValueError(f"Long format requires snapshot column '{spec.snapshot_col}' for index_mode='snapshot'")
            snap = pd.to_datetime(df[spec.snapshot_col], errors="raise")
        else:
            if spec.hour_col not in cols:
                raise ValueError(f"Long format requires hour column '{spec.hour_col}' for index_mode='hour'")
            if snapshots is None:
                n_hours = int(pd.to_numeric(df[spec.hour_col], errors="raise").max())
                base = pd.Timestamp("2000-01-01 00:00")
                snapshots = pd.date_range(base, periods=n_hours, freq="1h")
            # If price file is hourly but snapshots are coarser, downsample by averaging
            # within each slot block before mapping hours -> snapshots.
            df_hour = df[[region_col, spec.hour_col, spec.price_col]].copy()
            if snapshots is not None:
                hour = pd.to_numeric(df_hour[spec.hour_col], errors="raise").astype(int)
                if hour.max() > len(snapshots):
                    df_hour = _downsample_hour_index_to_snapshots_len(
                        df=df_hour,
                        hour_col=spec.hour_col,
                        snapshots_len=len(snapshots),
                    )
                    df_hour[spec.price_col] = pd.to_numeric(df_hour[spec.price_col], errors="raise")
                    df_hour = (
                        df_hour.groupby([region_col, spec.hour_col], as_index=False)[spec.price_col]
                        .mean(numeric_only=True)
                    )
            snap = _as_snapshots_from_hour(pd.Index(snapshots), df_hour[spec.hour_col])

        long = df_hour[[region_col, spec.price_col]].copy()
        long["_snapshot"] = pd.DatetimeIndex(snap)
        long[region_col] = long[region_col].astype(str)
        long[spec.price_col] = pd.to_numeric(long[spec.price_col], errors="raise")
        out = long.pivot_table(index="_snapshot", columns=region_col, values=spec.price_col, aggfunc="mean")

    # Align to target snapshots
    if snapshots is None:
        snap_dt = pd.DatetimeIndex(pd.to_datetime(out.index))
    else:
        snap_dt = pd.DatetimeIndex(pd.to_datetime(pd.Index(snapshots)))
    if spec.timezone is not None:
        # normalize both sides
        if out.index.tz is None:
            out.index = out.index.tz_localize(spec.timezone)
        out = out.tz_convert(spec.timezone)
        if snap_dt.tz is None:
            snap_dt = snap_dt.tz_localize(spec.timezone)
        snap_dt = snap_dt.tz_convert(spec.timezone)

    out = out.sort_index()
    out = out.reindex(snap_dt)

    if require_complete_hours:
        if out.isna().any().any():
            missing = out.isna().sum().sort_values(ascending=False)
            top = missing[missing > 0].head(10)
            raise ValueError(
                "Electricity prices have missing values after alignment. "
                f"Top missing columns:\n{top.to_string()}"
            )

    if require_non_negative and (out < 0).any().any():
        min_val = float(out.min().min())
        raise ValueError(f"Electricity prices contain negative values (min={min_val})")

    if expected_regions is not None:
        expected_regions = [str(x) for x in expected_regions]
        missing_cols = sorted(set(expected_regions) - set(map(str, out.columns)))
        if missing_cols:
            raise ValueError(f"Missing expected regions/buses in price data: {missing_cols[:20]}")

    return out


def extract_prices_from_network(
    *,
    network_path: str | Path,
    snapshots: pd.Index | None,
    expected_regions: list[str] | None = None,
    require_non_negative: bool = True,
) -> pd.DataFrame:
    """
    Extract nodal marginal prices from a solved PyPSA network netcdf file.

    Returns DataFrame with index=snapshots and columns=buses (AC buses by default).
    """

    network_path = Path(network_path)

    # Fast-path: open the netcdf and read only buses_t.marginal_price.
    price_df: pd.DataFrame | None = None
    xr_errors: list[str] = []
    try:
        # PyPSA netcdf files often store time-dependent tables in netcdf groups.
        # Try group="buses_t" first.
        try:
            ds = xr.open_dataset(network_path, group="buses_t", decode_times=True)
            if "marginal_price" in ds:
                price_df = ds["marginal_price"].to_pandas()
        except Exception as e:
            xr_errors.append(f"group=buses_t: {e!r}")

        if price_df is None:
            # Fallback: try root dataset and heuristic variable naming
            ds0 = xr.open_dataset(network_path, decode_times=True)
            if "buses_t_marginal_price" in ds0:
                # PyPSA netcdf uses an integer snapshot coordinate (0..N-1) and stores
                # actual timestamps in `snapshots_snapshot`.
                da = ds0["buses_t_marginal_price"]
                price_df = da.to_pandas()

                # Replace index with real timestamps if available
                if "snapshots_snapshot" in ds0:
                    snap = pd.to_datetime(ds0["snapshots_snapshot"].to_pandas(), errors="coerce")
                    if getattr(snap, "isna", lambda: False)().any():
                        raise ValueError("snapshots_snapshot contains invalid timestamps")
                    price_df.index = pd.DatetimeIndex(snap.values)

                # Prefer AC buses if carriers available
                if "buses_carrier" in ds0 and "buses_i" in ds0.coords:
                    buses_carrier = ds0["buses_carrier"].to_pandas()
                    # buses_carrier index is buses_i
                    ac_buses = buses_carrier.index[buses_carrier.astype(str) == "AC"]
                    # marginal_price columns use buses_t_marginal_price_i
                    ac_cols = [c for c in price_df.columns if c in set(ac_buses)]
                    if ac_cols:
                        price_df = price_df[ac_cols]
            else:
                # Heuristic fallback for other variable names
                candidates = []
                for name in list(ds0.data_vars):
                    s = str(name).lower()
                    if "marginal" in s and "price" in s and "bus" in s:
                        candidates.append(name)
                if candidates:
                    price_df = ds0[candidates[0]].to_pandas()
    except Exception as e:
        xr_errors.append(f"root: {e!r}")

    if price_df is None:
        # Slow-path: fall back to full PyPSA load
        import pypsa
        n = pypsa.Network(str(network_path))
        if not hasattr(n, "buses_t") or not hasattr(n.buses_t, "marginal_price"):
            raise ValueError(f"No buses_t.marginal_price found in network: {network_path}")

        # Prefer AC buses if present; otherwise keep all price columns
        if hasattr(n, "buses") and "carrier" in n.buses.columns and (n.buses.carrier == "AC").any():
            buses = n.buses.index[n.buses.carrier == "AC"]
            price_df = n.buses_t.marginal_price.reindex(columns=buses)
        else:
            price_df = n.buses_t.marginal_price.copy()

        price_df = price_df.copy()
        price_df.index = pd.DatetimeIndex(pd.to_datetime(price_df.index))
    else:
        price_df = price_df.copy()
        price_df.index = pd.DatetimeIndex(pd.to_datetime(price_df.index))

    if snapshots is not None:
        target = pd.DatetimeIndex(pd.to_datetime(pd.Index(snapshots)))

        # Align to target snapshots. Prefer time-based alignment; fall back to positional
        # alignment when the extracted index cannot be matched but lengths agree.
        aligned = price_df.reindex(target)
        if aligned.isna().all().all() and len(price_df.index) == len(target):
            aligned = price_df.copy()
            aligned.index = target
        price_df = aligned

    if price_df.isna().any().any():
        missing = price_df.isna().sum().sort_values(ascending=False)
        top = missing[missing > 0].head(10)
        raise ValueError(
            "Extracted prices have missing values after alignment to snapshots. "
            f"Top missing columns:\n{top.to_string()}"
        )

    if require_non_negative and (price_df < 0).any().any():
        min_val = float(price_df.min().min())
        raise ValueError(f"Extracted electricity prices contain negative values (min={min_val})")

    if expected_regions is not None:
        expected_regions = [str(x) for x in expected_regions]
        missing_cols = sorted(set(expected_regions) - set(map(str, price_df.columns)))
        if missing_cols:
            raise ValueError(f"Missing expected regions/buses in extracted price data: {missing_cols[:20]}")

    return price_df


def export_prices_to_csv(
    *,
    prices: pd.DataFrame,
    out_path: str | Path,
    spec: ElectricityPriceSpec,
    export_format: Literal["long", "wide"] = "long",
    region_mode: Literal["auto", "province", "bus"] = "bus",
    index_mode: Literal["auto", "hour", "snapshot"] = "hour",
    split_per_region: bool = False,
    write_combined: bool = True,
) -> None:
    """
    Export a price DataFrame (index=snapshots, columns=buses/regions) to a CSV file.
    """

    out_path = Path(out_path)
    df = prices.copy()

    if index_mode == "auto":
        index_mode = "hour"
    if region_mode == "auto":
        region_mode = "bus"

    if split_per_region:
        # Write one CSV per column/region into the same directory as out_path.
        # Naming: <stem>-<region>.csv
        out_dir = out_path.parent
        stem = out_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)

        for region in df.columns:
            region_str = str(region)
            single = df[[region]].copy()

            if export_format == "wide":
                wide = single.copy()
                if index_mode == "hour":
                    wide.insert(0, spec.hour_col, range(1, len(wide.index) + 1))
                else:
                    wide.insert(0, spec.snapshot_col, pd.DatetimeIndex(wide.index))
                wide.to_csv(out_dir / f"{stem}-{region_str}.csv", index=False)
            else:
                # long
                region_col = spec.bus_col if region_mode == "bus" else spec.region_col
                long = single.stack().rename(spec.price_col).reset_index()
                # The first two columns are snapshot + region; column names depend on index names.
                if len(long.columns) < 3:
                    raise ValueError("Unexpected stacked price table shape when exporting long format")
                snap_col_in = long.columns[0]
                region_col_in = long.columns[1]
                long = long.rename(columns={snap_col_in: "_snapshot", region_col_in: region_col})
                if index_mode == "hour":
                    snapshot_to_hour = pd.Series(
                        range(1, len(single.index) + 1),
                        index=pd.DatetimeIndex(single.index),
                    )
                    long[spec.hour_col] = long["_snapshot"].map(snapshot_to_hour)
                    long = long.drop(columns=["_snapshot"])
                else:
                    long = long.rename(columns={"_snapshot": spec.snapshot_col})
                long.to_csv(out_dir / f"{stem}-{region_str}.csv", index=False)

        if not write_combined:
            return
        # fall through to also write combined out_path

    if export_format == "wide":
        wide = df.copy()
        if index_mode == "hour":
            wide.insert(0, spec.hour_col, range(1, len(wide.index) + 1))
        else:
            wide.insert(0, spec.snapshot_col, pd.DatetimeIndex(wide.index))
        wide.to_csv(out_path, index=False)
        return

    # long
    region_col = spec.bus_col if region_mode == "bus" else spec.region_col
    long = df.stack().rename(spec.price_col).reset_index()
    if len(long.columns) < 3:
        raise ValueError("Unexpected stacked price table shape when exporting long format")
    snap_col_in = long.columns[0]
    region_col_in = long.columns[1]
    long = long.rename(columns={snap_col_in: "_snapshot", region_col_in: region_col})
    if index_mode == "hour":
        # hour index is 1..N in the order of snapshots
        snapshot_to_hour = pd.Series(range(1, len(df.index) + 1), index=pd.DatetimeIndex(df.index))
        long[spec.hour_col] = long["_snapshot"].map(snapshot_to_hour)
        long = long.drop(columns=["_snapshot"])
    else:
        long = long.rename(columns={"_snapshot": spec.snapshot_col})
    long.to_csv(out_path, index=False)

