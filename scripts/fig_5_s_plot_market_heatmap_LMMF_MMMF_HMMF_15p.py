# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Heatmap script comparing three aluminum smelter flexibility scenarios
LMMF / MMMF / HMMF (2050, 15p).

Notes:
- The three scenarios differ only in `smelter_flexibility = low/mid/high` (L/M/H),
  with demand fixed at M, market_opportunity fixed at M, and employment favorable (F).

This script is adapted from fig_5_s_plot_heatmap_comparison.py:
- Keep the original colormap, fonts, and overall style;
- Extend the original 2-row comparison to 3 rows;
- By default use 2050, 15% capacity (15p), favorable employment (F) with:
  - configs/config_LMMF_2050_15p.yaml
  - configs/config_MMMF_2050_15p.yaml
  - configs/config_HMMF_2050_15p.yaml

Only aluminum smelter operation (and its storage overlay) is plotted in this script.
"""

import yaml
import seaborn as sns
import pandas as pd
import pypsa
import matplotlib.pyplot as plt
import os
import argparse
from pathlib import Path


def set_plot_style():
    """Set plotting style (aligned with fig_5_s)."""
    plt.rcParams['font.sans-serif'] = ['Helvetica', 'Arial', 'sans-serif']
    plt.rcParams['axes.unicode_minus'] = False

    plt.style.use(
        [
            'classic',
            'seaborn-v0_8-whitegrid',
            {
                'axes.grid': False,
                'grid.linestyle': '--',
                'grid.color': u'0.6',
                'hatch.color': 'white',
                'patch.linewidth': 0.5,
                'font.size': 22,
                'legend.fontsize': 25,
                'ytick.labelsize': 22,
                'lines.linewidth': 1.5,
                'pdf.fonttype': 42,
            },
        ]
    )


def create_df(n, tech, province_filter=None):
    """Create a heatmap-ready dataframe for a given storage technology (as in fig_5_s)."""
    stores = n.stores_t.p.filter(like=tech)

    if province_filter:
        province_stores = stores.columns[stores.columns.str.contains(province_filter, case=False)]
        if len(province_stores) > 0:
            stores = stores[province_stores]
        else:
            print(f"Warning: No {tech} storage found in province {province_filter}")
            return pd.DataFrame(), 0

    base = abs(stores.sum(axis=1)).max()
    if base == 0:
        print(
            f"Warning: {tech} power is zero "
            f"{'in province ' + province_filter if province_filter else 'nationwide'}"
        )
        return pd.DataFrame(), 0

    df = stores.sum(axis=1) / base
    df = df.to_frame()
    df.reset_index(inplace=True)
    df.rename(columns={0: 'p_store'}, inplace=True)

    date = n.stores_t.p.filter(like='water').index
    df['Hour'] = date.hour
    df['Day'] = date.strftime('%m-%d')

    summary = pd.pivot_table(data=df, index='Hour', columns='Day', values='p_store')
    summary = summary.fillna(0).infer_objects(copy=False)
    return summary, base


def create_aluminum_df(n, province_filter=None):
    """Create a heatmap-ready dataframe for aluminum smelter operation (as in fig_5_s)."""
    aluminum_links = n.links_t.p0.filter(like='aluminum smelter')

    if aluminum_links.empty:
        print("Warning: No aluminum smelter links found in network")
        return pd.DataFrame(), 0

    if province_filter:
        province_smelters = aluminum_links.columns[aluminum_links.columns.str.contains(province_filter, case=False)]
        if len(province_smelters) > 0:
            aluminum_links = aluminum_links[province_smelters]
        else:
            print(f"Warning: No aluminum smelters found in province {province_filter}")
            return pd.DataFrame(), 0

    base = abs(aluminum_links.sum(axis=1)).max()
    if base == 0:
        print(
            "Warning: Aluminum smelter power is zero "
            f"{'in province ' + province_filter if province_filter else 'nationwide'}"
        )
        return pd.DataFrame(), 0

    df = aluminum_links.sum(axis=1) / base
    df = df.to_frame()
    df.reset_index(inplace=True)
    df.rename(columns={0: 'p_smelter'}, inplace=True)

    date = aluminum_links.index
    df['Hour'] = date.hour
    df['Day'] = date.strftime('%m-%d')

    summary = pd.pivot_table(data=df, index='Hour', columns='Day', values='p_smelter')
    summary = summary.fillna(0).infer_objects(copy=False)
    return summary, base


def get_aluminum_storage_daily_average(n, province_filter=None):
    """Get daily-average aluminum storage to overlay on the heatmap (as in fig_5_s)."""
    aluminum_stores = n.stores_t.e.filter(like='aluminum storage')

    if aluminum_stores.empty:
        print("Warning: No aluminum storage found in network")
        return pd.Series(), 0

    if province_filter:
        province_stores = aluminum_stores.columns[aluminum_stores.columns.str.contains(province_filter, case=False)]
        if len(province_stores) > 0:
            aluminum_stores = aluminum_stores[province_stores]
        else:
            print(f"Warning: No aluminum storage found in province {province_filter}")
            return pd.Series(), 0

    total_storage = aluminum_stores.sum(axis=1)
    if total_storage.empty:
        print(
            "Warning: Aluminum storage data is empty "
            f"{'in province ' + province_filter if province_filter else 'nationwide'}"
        )
        return pd.Series(), 0

    min_storage = total_storage.min()
    daily_avg = total_storage.groupby(total_storage.index.strftime('%m-%d')).mean()
    daily_avg_normalized = daily_avg - min_storage

    return daily_avg_normalized, min_storage


def plot_market_comparison_heatmap(
    n_lmmf, n_mmmf, n_hmmf, config, output_dir, tech, province_filter=None
):
    """
    Generate comparison heatmap for LMMF / MMMF / HMMF smelter-flexibility scenarios (3 rows).
    """
    if province_filter:
        province_dir = os.path.join(output_dir, f"province_{province_filter}")
        os.makedirs(province_dir, exist_ok=True)
        plot_title_suffix = f" in {province_filter}"
    else:
        province_dir = output_dir
        plot_title_suffix = " (National)"

    # Three stacked subplots; overall aspect ratio near square
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(12, 16))

    # ---- LMMF ----
    if tech == "aluminum":
        df_l, base_l = create_aluminum_df(n_lmmf, province_filter)
        daily_storage_l, _ = get_aluminum_storage_daily_average(n_lmmf, province_filter)
    else:
        df_l, base_l = create_df(n_lmmf, tech, province_filter)
        daily_storage_l = None

    if not df_l.empty and base_l > 0:
        if tech == "aluminum":
            sns.heatmap(
                df_l,
                ax=ax1,
                cmap='coolwarm',
                cbar_kws={'label': 'pu', 'shrink': 0.8},
                vmin=0.0,
                vmax=1.0,
            )
        else:
            sns.heatmap(
                df_l,
                ax=ax1,
                cmap='coolwarm',
                cbar_kws={'label': 'pu', 'shrink': 0.8},
                vmin=-1.0,
                vmax=1.0,
            )

        ax1.set_title(f'Low smelter flexibility (LMMF){plot_title_suffix}')
        ax1.set_xlabel('')
        ax1.set_yticklabels(ax1.get_yticklabels(), rotation=0)

        if tech == "aluminum" and daily_storage_l is not None and not daily_storage_l.empty:
            day_columns = df_l.columns
            storage_values = []
            storage_positions = []
            for i, day in enumerate(day_columns):
                if day in daily_storage_l.index:
                    storage_values.append(daily_storage_l[day] / 1e6)
                    storage_positions.append(i + 0.5)
            if storage_values:
                ax1_twin = ax1.twinx()
                ax1_twin.plot(storage_positions, storage_values, 'w-', linewidth=3.5, zorder=1)
                ax1_twin.plot(storage_positions, storage_values, 'k-', linewidth=2, zorder=2)
                ax1_twin.set_ylabel('Stored aluminum (Mt)', color='black')
                ax1_twin.tick_params(axis='y', labelcolor='black')
                ax1_twin.set_xlim(0, len(day_columns))

    # ---- MMMF ----
    if tech == "aluminum":
        df_m, base_m = create_aluminum_df(n_mmmf, province_filter)
        daily_storage_m, _ = get_aluminum_storage_daily_average(n_mmmf, province_filter)
    else:
        df_m, base_m = create_df(n_mmmf, tech, province_filter)
        daily_storage_m = None

    if not df_m.empty and base_m > 0:
        if tech == "aluminum":
            sns.heatmap(
                df_m,
                ax=ax2,
                cmap='coolwarm',
                cbar_kws={'label': 'pu', 'shrink': 0.8},
                vmin=0.0,
                vmax=1.0,
            )
        else:
            sns.heatmap(
                df_m,
                ax=ax2,
                cmap='coolwarm',
                cbar_kws={'label': 'pu', 'shrink': 0.8},
                vmin=-1.0,
                vmax=1.0,
            )

        ax2.set_title(f'Mid smelter flexibility (MMMF){plot_title_suffix}')
        ax2.set_xlabel('')
        ax2.set_yticklabels(ax2.get_yticklabels(), rotation=0)

        if tech == "aluminum" and daily_storage_m is not None and not daily_storage_m.empty:
            day_columns = df_m.columns
            storage_values = []
            storage_positions = []
            for i, day in enumerate(day_columns):
                if day in daily_storage_m.index:
                    storage_values.append(daily_storage_m[day] / 1e6)
                    storage_positions.append(i + 0.5)
            if storage_values:
                ax2_twin = ax2.twinx()
                ax2_twin.plot(storage_positions, storage_values, 'w-', linewidth=3.5, zorder=1)
                ax2_twin.plot(storage_positions, storage_values, 'k-', linewidth=2, zorder=2)
                ax2_twin.set_ylabel('Stored aluminum (Mt)', color='black')
                ax2_twin.tick_params(axis='y', labelcolor='black')
                ax2_twin.set_xlim(0, len(day_columns))

    # ---- HMMF ----
    if tech == "aluminum":
        df_h, base_h = create_aluminum_df(n_hmmf, province_filter)
        daily_storage_h, _ = get_aluminum_storage_daily_average(n_hmmf, province_filter)
    else:
        df_h, base_h = create_df(n_hmmf, tech, province_filter)
        daily_storage_h = None

    if not df_h.empty and base_h > 0:
        if tech == "aluminum":
            sns.heatmap(
                df_h,
                ax=ax3,
                cmap='coolwarm',
                cbar_kws={'label': 'pu', 'shrink': 0.8},
                vmin=0.0,
                vmax=1.0,
            )
        else:
            sns.heatmap(
                df_h,
                ax=ax3,
                cmap='coolwarm',
                cbar_kws={'label': 'pu', 'shrink': 0.8},
                vmin=-1.0,
                vmax=1.0,
            )

        ax3.set_title(f'High smelter flexibility (HMMF){plot_title_suffix}')
        ax3.set_xlabel('Day')
        ax3.set_yticklabels(ax3.get_yticklabels(), rotation=0)

        if tech == "aluminum" and daily_storage_h is not None and not daily_storage_h.empty:
            day_columns = df_h.columns
            storage_values = []
            storage_positions = []
            for i, day in enumerate(day_columns):
                if day in daily_storage_h.index:
                    storage_values.append(daily_storage_h[day] / 1e6)
                    storage_positions.append(i + 0.5)
            if storage_values:
                ax3_twin = ax3.twinx()
                ax3_twin.plot(storage_positions, storage_values, 'w-', linewidth=3.5, zorder=1)
                ax3_twin.plot(storage_positions, storage_values, 'k-', linewidth=2, zorder=2)
                ax3_twin.set_ylabel('Stored aluminum (Mt)', color='black')
                ax3_twin.tick_params(axis='y', labelcolor='black')
                ax3_twin.set_xlim(0, len(day_columns))

    plt.tight_layout()
    plt.subplots_adjust(right=2)

    # Save figure
    fname = f"flexibility_LMMF_MMMF_HMMF_2050_15p_{tech}.png"
    output_path = os.path.join(province_dir, fname)
    fig.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"Saved {tech} market comparison heatmap to: {output_path}")
    plt.close()


def main():
    """Main entry point: compare 2050 LMMF / MMMF / HMMF (15p, F) smelter flexibility scenarios."""
    parser = argparse.ArgumentParser(
        description='Compare heatmaps between 2050 LMMF/MMMF/HMMF (15p, favorable employment; different smelter flexibility)'
    )
    parser.add_argument(
        '--config-lmmf',
        type=str,
        default='configs/config_LMMF_2050_15p.yaml',
        help='LMMF configuration file path',
    )
    parser.add_argument(
        '--config-mmmf',
        type=str,
        default='configs/config_MMMF_2050_15p.yaml',
        help='MMMF configuration file path',
    )
    parser.add_argument(
        '--config-hmmf',
        type=str,
        default='configs/config_HMMF_2050_15p.yaml',
        help='HMMF configuration file path',
    )
    parser.add_argument(
        '--output-dir',
        type=str,
        default='results/comparison_heatmaps_flexibility_2050_15p',
        help='Output directory',
    )
    parser.add_argument(
        '--province',
        type=str,
        default=None,
        help='Province filter (optional)',
    )
    parser.add_argument(
        '--techs',
        nargs='+',
        default=['aluminum'],
        help='Technologies to plot (this script is intended for aluminum only)',
    )

    args = parser.parse_args()

    set_plot_style()

    print("Loading configuration files...")
    with open(args.config_lmmf, 'r', encoding='utf-8') as f:
        config_lmmf = yaml.safe_load(f)
    with open(args.config_mmmf, 'r', encoding='utf-8') as f:
        config_mmmf = yaml.safe_load(f)
    with open(args.config_hmmf, 'r', encoding='utf-8') as f:
        config_hmmf = yaml.safe_load(f)

    # Use `version` from configs to build network paths
    lmmf_version = config_lmmf['version']
    mmmf_version = config_mmmf['version']
    hmmf_version = config_hmmf['version']

    def _pick_network_path(version: str) -> str:
        candidates = [
            f"results/version-{version}/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2050.nc",
            f"results/version-{version}/postnetworks/positive/postnetwork-ll-current+Neighbor-linear2050-2050.nc",
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return candidates[0]

    lmmf_network_path = _pick_network_path(lmmf_version)
    mmmf_network_path = _pick_network_path(mmmf_version)
    hmmf_network_path = _pick_network_path(hmmf_version)

    # Check that network files exist
    if not os.path.exists(lmmf_network_path):
        print(f"Error: LMMF network file not found: {lmmf_network_path}")
        return
    if not os.path.exists(mmmf_network_path):
        print(f"Error: MMMF network file not found: {mmmf_network_path}")
        return
    if not os.path.exists(hmmf_network_path):
        print(f"Error: HMMF network file not found: {hmmf_network_path}")
        return

    print("Loading network data...")
    n_lmmf = pypsa.Network(lmmf_network_path)
    n_mmmf = pypsa.Network(mmmf_network_path)
    n_hmmf = pypsa.Network(hmmf_network_path)

    os.makedirs(args.output_dir, exist_ok=True)

    print("Generating smelter flexibility comparison heatmaps (aluminum only)...")
    # Even if the user passes other techs, we only plot aluminum as requested.
    plot_market_comparison_heatmap(
        n_lmmf, n_mmmf, n_hmmf, config_mmmf, args.output_dir, "aluminum", args.province
    )

    print("All smelter flexibility comparison heatmaps generated successfully!")


if __name__ == "__main__":
    main()

