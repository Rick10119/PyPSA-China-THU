# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Compare employment between two scenarios: MMMU and MMMU_non_flexible.

Reads CSV files with _15p (MMMU) and _non_flexible (MMMU_non_flexible) suffixes
from results/monthly_capacity_factors and produces a two-panel comparison plot.
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import os
import argparse
import yaml
from pathlib import Path

# Publication: sans-serif (Helvetica / Arial), 6 pt, figure width 150 mm
TEXT_PT = 7
FIG_WIDTH_MM = 150
# Match prior aspect ratios: main plot was 12×9 in; mean/variance was 15×6 in
FIG_EMPLOYMENT_COMPARISON_HEIGHT_MM = FIG_WIDTH_MM * 8 / 15
FIG_MEAN_VAR_HEIGHT_MM = FIG_WIDTH_MM * 6 / 15


def _mm_to_inches(mm: float) -> float:
    return mm / 25.4


def set_plot_style():
    """
    Set plotting style: sans-serif 6 pt, PDF-friendly fonts.
    """
    plt.style.use(
        [
            "classic",
            "seaborn-v0_8-whitegrid",
            {
                "axes.grid": False,
                "grid.linestyle": "--",
                "grid.color": "0.6",
                "hatch.color": "white",
                "patch.linewidth": 0.5,
                "lines.linewidth": 1.5,
            },
        ]
    )
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Helvetica", "Arial", "Helvetica Neue", "DejaVu Sans"],
            "font.size": TEXT_PT,
            "axes.labelsize": TEXT_PT,
            "axes.titlesize": TEXT_PT,
            "xtick.labelsize": TEXT_PT,
            "ytick.labelsize": TEXT_PT,
            "legend.fontsize": TEXT_PT,
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
        }
    )

def load_csv_data(csv_file):
    """
    Load capacity factor data from CSV; use average factors only.

    Parameters:
    -----------
    csv_file : str
        Path to CSV file

    Returns:
    --------
    tuple
        (capacity_factors, load_factors) two DataFrames
    """
    if not os.path.exists(csv_file):
        raise FileNotFoundError(f"CSV file not found: {csv_file}")

    df = pd.read_csv(csv_file, index_col='Month')

    # Split average capacity factor and load factor columns
    avg_capacity_cols = [col for col in df.columns if 'Capacity_Factor_Avg' in col]
    load_cols = [col for col in df.columns if 'Load_Factor' in col]

    if avg_capacity_cols:
        capacity_factors = df[avg_capacity_cols]
        capacity_factors.columns = [col.replace('_Capacity_Factor_Avg', '') for col in capacity_factors.columns]
        print(f"Using monthly average capacity factor data for employment: {os.path.basename(csv_file)}")
        
    else:
        # Legacy format: only Capacity_Factor columns
        capacity_cols = [col for col in df.columns if 'Capacity_Factor' in col and 'Max' not in col and 'Avg' not in col]
        capacity_factors = df[capacity_cols] if capacity_cols else pd.DataFrame()
        capacity_factors.columns = [col.replace('_Capacity_Factor', '') for col in capacity_factors.columns]
        print(f"Using legacy capacity factor data for employment: {os.path.basename(csv_file)}")
    
    load_factors = df[load_cols] if load_cols else pd.DataFrame()
    load_factors.columns = [col.replace('_Load_Factor', '') for col in load_factors.columns]
    
    return capacity_factors, load_factors

def load_employment_config(config_file=None):
    """
    Load employment parameters from config file.

    Parameters:
    -----------
    config_file : str, optional
        Config file path; default employment_config.yaml

    Returns:
    --------
    tuple
        (employment_params, config) employment params dict and config dict
    """
    if config_file is None:
        config_file = os.path.join(os.path.dirname(__file__), 'employment_config.yaml')
    
    if not os.path.exists(config_file):
        print(f"Warning: Config file {config_file} not found, using default parameters")
        return get_default_employment_parameters(), {}
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        employment_params = {}
        for tech, params in config['industries'].items():
            employment_params[tech] = {
                'installed_capacity': params['installed_capacity'],
                'employment_per_gw': params['employment_per_GW'],
                'display_name': params['display_name'],
                'unit_system': 'GW'
            }

        
        return employment_params, config
    except Exception as e:
        print(f"Warning: Failed to read config file: {e}, using default parameters")
        return get_default_employment_parameters(), {}

def get_default_employment_parameters():
    """
    Return default employment parameters per industry.

    Returns:
    --------
    dict
        Industry parameters (installed capacity GW, employment per GW).
    """
    employment_params = {
        'Aluminum': {
            'installed_capacity': 16.5,   # GW - aluminum smelter
            'employment_per_GW': 15.4,    # persons per GW
            'display_name': 'Aluminum Smelter'
        },
        'Coal': {
            'installed_capacity': 343.0,  # GW - coal power
            'employment_per_GW': 0.8,     # persons per GW
            'display_name': 'Coal Power'
        },
        'Gas': {
            'installed_capacity': 100.80, # GW - gas power
            'employment_per_GW': 0.2,     # persons per GW
            'display_name': 'Gas Power'
        }
    }
    
    return employment_params

def get_scenario_specific_parameters(scenario_type):
    """
    Get employment parameters for a given scenario.

    Parameters
    ----------
    scenario_type : str
        One of 'MMMU' or 'MMMU_non_flexible'.

    Returns
    -------
    dict
        Industry parameters (installed_capacity, employment_per_GW, display_name).
    """
    if scenario_type == 'MMMU_non_flexible':
        # MMMU_non_flexible (decommission all overcapacity)
        employment_params = {
            'Aluminum': {
                'installed_capacity': 11.7,   # GW - aluminum smelter
                'employment_per_GW': 15.4,     # persons per GW
                'display_name': 'Aluminum Smelter'
            },
            'Coal': {
                'installed_capacity': 343.7,  # GW - coal power
                'employment_per_GW': 0.8,     # persons per GW
                'display_name': 'Coal Power'
            },
            'Gas': {
                'installed_capacity': 100.80, # GW - gas power
                'employment_per_GW': 0.2,     # persons per GW
                'display_name': 'Gas Power'
            }
        }
    else:
        # MMMU (flexible, e.g. 15p overcapacity) uses default parameters
        employment_params = get_default_employment_parameters()

    return employment_params

def calculate_monthly_employment(capacity_factors, employment_params):
    """
    Compute monthly employment from average capacity factors.

    Parameters:
    -----------
    capacity_factors : pd.DataFrame
        Average capacity factors, rows=months, columns=technology
    employment_params : dict
        Installed capacity and employment per GW per industry

    Returns:
    --------
    pd.DataFrame
        Monthly employment by industry
    """
    employment_data = {}

    for tech, params in employment_params.items():
        if tech in capacity_factors.columns:
            # Employment = (0.1 + 0.9 * capacity_factor) * installed_capacity * employment_per_GW
            monthly_employment = (0.10 + 0.90 * capacity_factors[tech]) * (
                                params['installed_capacity'] * 
                                params['employment_per_GW'])
            employment_data[params['display_name']] = monthly_employment

    
    return pd.DataFrame(employment_data, index=capacity_factors.index)

def plot_employment_comparison(employment_data_15p, employment_data_non_flexible,
                             output_file=None, config=None, differences=None):
    """
    Plot employment comparison for MMMU vs MMMU_non_flexible (two panels).
    
    Parameters
    ----------
    employment_data_15p : pd.DataFrame
        Monthly employment for MMMU scenario.
    employment_data_non_flexible : pd.DataFrame
        Monthly employment for MMMU_non_flexible scenario.
    output_file : str, optional
        Output plot file path
    config : dict, optional
        Config dict (e.g. colors)
    differences : dict, optional
        Stats dict for mean/variance of total employment on the plot
    """
    if employment_data_15p.empty or employment_data_non_flexible.empty:
        print("Warning: No employment data to plot")
        return

    set_plot_style()
    font_size = TEXT_PT
    legend_font_size = TEXT_PT
    title_font_size = TEXT_PT
    axis_font_size = TEXT_PT

    fig, (ax1, ax2) = plt.subplots(
        2,
        1,
        figsize=(
            _mm_to_inches(FIG_WIDTH_MM),
            _mm_to_inches(FIG_EMPLOYMENT_COMPARISON_HEIGHT_MM),
        ),
        sharex=False,
    )

    if config and 'colors' in config:
        colors = config['colors']
    else:
        colors = {
            'Aluminum Smelter': '#FF69B4',    # Hot pink
            'Coal Power': '#000000',          # Black
            'Gas Power': '#FF0000'            # Red
        }
    
    legend_labels = {
        'Aluminum Smelter': 'Aluminum smelters',
        'Coal Power': 'Coal power plants',
        'Gas Power': 'Gas power plants'
    }
    
    # Upper panel: MMMU
    plot_single_scenario(ax1, employment_data_15p, colors, "MMMU", 
                        font_size, legend_font_size, axis_font_size, show_legend=False,
                        legend_labels=legend_labels)
    
    # Lower panel: MMMU_non_flexible
    plot_single_scenario(ax2, employment_data_non_flexible, colors, "MMMU (non-flexible)", 
                        font_size, legend_font_size, axis_font_size, show_legend=False,
                        legend_labels=legend_labels)
    
    ax1.set_ylim(0, 500)
    ax2.set_ylim(0, 500)

    ax1.set_title('Retaining 30% overcapacity', fontsize=title_font_size, pad=6)
    ax2.set_title('Retaining 0% overcapacity', fontsize=title_font_size, pad=6)
    ax2.set_xlabel("Month", fontsize=axis_font_size)

    handles, labels = ax1.get_legend_handles_labels()

    plt.tight_layout()
    # Anchor legend just below ax2; fixed figure y (e.g. 0.06) + tight_layout(rect bottom)
    # left a large empty band between panels and legend.
    pos2 = ax2.get_position()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, pos2.y0 - 0.065),
        bbox_transform=fig.transFigure,
        ncol=len(handles),
        fontsize=legend_font_size,
        frameon=False,
    )

    if output_file is None:
        output_file = "employment_scenario_comparison.pdf"

    fig.savefig(output_file, format="pdf", bbox_inches="tight", facecolor="white")
    # plt.show()
    plt.close()
    
    print(f"Employment comparison plot saved to: {output_file}")

def plot_single_scenario(ax, employment_data, colors, scenario_name,
                        font_size, legend_font_size, axis_font_size, show_legend=True,
                        legend_labels=None):
    """
    Plot employment for a single scenario.

    Parameters:
    -----------
    ax : matplotlib.axes.Axes
        Axes to draw on
    employment_data : pd.DataFrame
        Monthly employment by industry
    colors : dict
        Color map for industries
    scenario_name : str
        Scenario label
    font_size, legend_font_size, axis_font_size : int
        Font sizes
    show_legend : bool
        Whether to show legend
    """
    # Put Aluminum Smelter on top in stacking order
    column_order = []
    if 'Aluminum Smelter' in employment_data.columns:
        column_order.append('Aluminum Smelter')
    for col in employment_data.columns:
        if col != 'Aluminum Smelter':
            column_order.append(col)
    
    employment_data_ordered = employment_data[column_order]

    cumulative_data = employment_data_ordered.cumsum(axis=1)

    for i, industry in enumerate(employment_data_ordered.columns):
        months = employment_data_ordered.index
        label = legend_labels.get(industry, industry) if legend_labels else industry

        if i == 0:
            values = cumulative_data[industry].values
            ax.fill_between(months, 0, values, color=colors.get(industry, '#808080'), 
                           alpha=0.7, label=label)
        else:
            prev_industry = employment_data_ordered.columns[i-1]
            prev_values = cumulative_data[prev_industry].values
            values = cumulative_data[industry].values
            ax.fill_between(months, prev_values, values, color=colors.get(industry, '#808080'), 
                           alpha=0.7, label=label)
    
    ax.set_ylabel('Needed workforce', fontsize=axis_font_size)
    ax.set_xlim(1.0, 12.0)
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], fontsize=font_size)

    ax.set_yticks(range(0, 401, 100))
    ax.set_yticklabels([f'{i}k' for i in range(0, 401, 100)], fontsize=font_size)

    ax.tick_params(axis='x', labelsize=font_size)
    ax.tick_params(axis='y', labelsize=font_size)

    ax.grid(True, alpha=0.3)

    if show_legend:
        ax.legend(loc='best', fontsize=legend_font_size)

def plot_mean_variance_comparison(differences, output_file=None):
    """
    Plot mean and variance comparison between scenarios.

    Parameters:
    -----------
    differences : dict
        Per-industry difference statistics
    output_file : str, optional
        Output plot file path
    """
    if not differences:
        print("Warning: No data to plot")
        return

    set_plot_style()

    industries = list(differences.keys())
    means_15p = [differences[industry]['avg_15p'] for industry in industries]
    means_non_flexible = [differences[industry]['avg_non_flexible'] for industry in industries]
    vars_15p = [differences[industry]['var_15p'] for industry in industries]
    vars_non_flexible = [differences[industry]['var_non_flexible'] for industry in industries]
    stds_15p = [differences[industry]['std_15p'] for industry in industries]
    stds_non_flexible = [differences[industry]['std_non_flexible'] for industry in industries]
    
    fig, (ax1, ax2) = plt.subplots(
        1,
        2,
        figsize=(
            _mm_to_inches(FIG_WIDTH_MM),
            _mm_to_inches(FIG_MEAN_VAR_HEIGHT_MM),
        ),
    )

    x = np.arange(len(industries))
    width = 0.35
    
    bars1 = ax1.bar(x - width/2, means_15p, width, label='MMMU', alpha=0.8, color='#FF69B4')
    bars2 = ax1.bar(x + width/2, means_non_flexible, width, label='MMMU (non-flexible)', alpha=0.8, color='#000000')
    
    ax1.set_xlabel('Industry', fontsize=TEXT_PT)
    ax1.set_ylabel('Average Employment (k)', fontsize=TEXT_PT)
    ax1.set_title('Average Employment by Industry', fontsize=TEXT_PT)
    ax1.set_xticks(x)
    ax1.set_xticklabels(industries, rotation=45, ha='right', fontsize=TEXT_PT)
    ax1.legend(fontsize=TEXT_PT)
    ax1.grid(True, alpha=0.3)

    for bar in bars1:
        height = bar.get_height()
        ax1.text(
            bar.get_x() + bar.get_width() / 2.0,
            height + 1,
            f'{height:.1f}',
            ha='center',
            va='bottom',
            fontsize=TEXT_PT,
        )

    for bar in bars2:
        height = bar.get_height()
        ax1.text(
            bar.get_x() + bar.get_width() / 2.0,
            height + 1,
            f'{height:.1f}',
            ha='center',
            va='bottom',
            fontsize=TEXT_PT,
        )

    bars3 = ax2.bar(x - width/2, vars_15p, width, label='MMMU', alpha=0.8, color='#FF69B4')
    bars4 = ax2.bar(x + width/2, vars_non_flexible, width, label='MMMU (non-flexible)', alpha=0.8, color='#000000')
    
    ax2.set_xlabel('Industry', fontsize=TEXT_PT)
    ax2.set_ylabel('Variance', fontsize=TEXT_PT)
    ax2.set_title('Employment Variance by Industry', fontsize=TEXT_PT)
    ax2.set_xticks(x)
    ax2.set_xticklabels(industries, rotation=45, ha='right', fontsize=TEXT_PT)
    ax2.legend(fontsize=TEXT_PT)
    ax2.grid(True, alpha=0.3)

    # Annotate each variance bar with its variance and standard deviation
    for i, bar in enumerate(bars3):
        var_val = bar.get_height()
        std_val = stds_15p[i]
        ax2.text(
            bar.get_x() + bar.get_width() / 2.0,
            var_val + max(var_val * 0.01, 0.05),
            f'{var_val:.2f}\nσ={std_val:.2f}',
            ha='center',
            va='bottom',
            fontsize=TEXT_PT,
        )

    for i, bar in enumerate(bars4):
        var_val = bar.get_height()
        std_val = stds_non_flexible[i]
        ax2.text(
            bar.get_x() + bar.get_width() / 2.0,
            var_val + max(var_val * 0.01, 0.05),
            f'{var_val:.2f}\nσ={std_val:.2f}',
            ha='center',
            va='bottom',
            fontsize=TEXT_PT,
        )
    
    plt.tight_layout()

    if output_file is None:
        output_file = "mean_variance_comparison.pdf"

    fig.savefig(output_file, format="pdf", bbox_inches="tight", facecolor="white")
    plt.close()
    
    print(f"Mean and variance comparison plot saved to: {output_file}")

def calculate_scenario_differences(employment_data_15p, employment_data_non_flexible):
    """
    Compute difference statistics between MMMU and MMMU_non_flexible.
    
    Parameters
    ----------
    employment_data_15p : pd.DataFrame
        Monthly employment for MMMU scenario.
    employment_data_non_flexible : pd.DataFrame
        Monthly employment for MMMU_non_flexible scenario.
    
    Returns
    -------
    dict
        Per-industry stats (avg_15p, avg_non_flexible, var_*, std_*, etc.).
    """
    differences = {}

    common_columns = set(employment_data_15p.columns) & set(employment_data_non_flexible.columns)

    for industry in common_columns:
        data_15p = employment_data_15p[industry]
        data_non_flexible = employment_data_non_flexible[industry]

        diff = data_15p - data_non_flexible
        diff_percent = (diff / data_non_flexible) * 100

        mean_15p = data_15p.mean()
        mean_non_flexible = data_non_flexible.mean()
        var_15p = data_15p.var()
        var_non_flexible = data_non_flexible.var()
        std_15p = data_15p.std()
        std_non_flexible = data_non_flexible.std()

        mean_diff = diff.mean()
        var_diff = diff.var()
        std_diff = diff.std()
        
        differences[industry] = {
            'absolute_diff': diff,
            'percent_diff': diff_percent,
            'avg_15p': mean_15p,
            'avg_non_flexible': mean_non_flexible,
            'var_15p': var_15p,
            'var_non_flexible': var_non_flexible,
            'std_15p': std_15p,
            'std_non_flexible': std_non_flexible,
            'total_15p': data_15p.sum(),
            'total_non_flexible': data_non_flexible.sum(),
            'max_diff': diff.max(),
            'min_diff': diff.min(),
            'avg_diff': mean_diff,
            'var_diff': var_diff,
            'std_diff': std_diff,
            'avg_percent_diff': diff_percent.mean()
        }
    
    return differences

def print_mean_variance_summary(differences):
    """
    Print mean and variance summary statistics.

    Parameters:
    -----------
    differences : dict
        Per-industry difference statistics
    """
    print(f"\nMean and variance statistics summary")
    print("=" * 60)
    
    for industry, stats in differences.items():
        print(f"\n{industry}:")
        print(f"  MMMU scenario:")
        print(f"    Mean: {stats['avg_15p']:.1f}k")
        print(f"    Variance: {stats['var_15p']:.2f}")
        print(f"    Std dev: {stats['std_15p']:.1f}k")
        print(f"  MMMU (non-flexible) scenario:")
        print(f"    Mean: {stats['avg_non_flexible']:.1f}k")
        print(f"    Variance: {stats['var_non_flexible']:.2f}")
        print(f"    Std dev: {stats['std_non_flexible']:.1f}k")
        print(f"  Difference statistics:")
        print(f"    Mean difference: {stats['avg_diff']:.1f}k ({stats['avg_percent_diff']:.1f}%)")
        print(f"    Variance of difference: {stats['var_diff']:.2f}")
        print(f"    Std dev of difference: {stats['std_diff']:.1f}k")

def print_comparison_statistics(differences, employment_params_15p, employment_params_non_flexible):
    """
    Print comparison statistics.

    Parameters:
    -----------
    differences : dict
        Per-industry difference statistics
    employment_params_15p : dict
        MMMU scenario employment parameters
    employment_params_non_flexible : dict
        MMMU_non_flexible scenario employment parameters
    """
    print(f"\nEmployment Scenario Comparison Statistics")
    print("=" * 80)

    print(f"\nScenario Parameters:")
    print(f"MMMU scenario:")
    for tech, params in employment_params_15p.items():
        print(f"  {params['display_name']}: {params['installed_capacity']} GW")
    
    print(f"MMMU (non-flexible) scenario:")
    for tech, params in employment_params_non_flexible.items():
        print(f"  {params['display_name']}: {params['installed_capacity']} GW")
    
    # Print mean/variance summary
    print_mean_variance_summary(differences)
    
    print(f"\nDetailed statistics:")
    for industry, stats in differences.items():
        print(f"\n{industry}:")
        print(f"  Average Employment - MMMU: {stats['avg_15p']:.1f}k")
        print(f"  Average Employment - MMMU (non-flexible): {stats['avg_non_flexible']:.1f}k")
        print(f"  Variance - MMMU: {stats['var_15p']:.2f}")
        print(f"  Variance - MMMU (non-flexible): {stats['var_non_flexible']:.2f}")
        print(f"  Standard Deviation - MMMU: {stats['std_15p']:.1f}k")
        print(f"  Standard Deviation - MMMU (non-flexible): {stats['std_non_flexible']:.1f}k")
        print(f"  Average Difference: {stats['avg_diff']:.1f}k ({stats['avg_percent_diff']:.1f}%)")
        print(f"  Variance of Difference: {stats['var_diff']:.2f}")
        print(f"  Standard Deviation of Difference: {stats['std_diff']:.1f}k")
        print(f"  Total Annual - MMMU: {stats['total_15p']:.1f}k")
        print(f"  Total Annual - MMMU (non-flexible): {stats['total_non_flexible']:.1f}k")
        print(f"  Max Monthly Difference: {stats['max_diff']:.1f}k")
        print(f"  Min Monthly Difference: {stats['min_diff']:.1f}k")

def save_comparison_data(employment_data_15p, employment_data_non_flexible,
                        differences, output_file=None):
    """
    Save MMMU vs MMMU_non_flexible comparison to CSV.
    
    Parameters
    ----------
    employment_data_15p : pd.DataFrame
        Monthly employment for MMMU scenario.
    employment_data_non_flexible : pd.DataFrame
        Monthly employment for MMMU_non_flexible scenario.
    differences : dict
        Per-industry difference statistics.
    output_file : str, optional
        Output CSV path.
    """
    if output_file is None:
        output_file = "employment_scenario_comparison.csv"

    comparison_data = {}
    for industry in employment_data_15p.columns:
        comparison_data[f"{industry}_15p"] = employment_data_15p[industry]
    for industry in employment_data_non_flexible.columns:
        comparison_data[f"{industry}_non_flexible"] = employment_data_non_flexible[industry]
    for industry, stats in differences.items():
        comparison_data[f"{industry}_difference"] = stats['absolute_diff']
        comparison_data[f"{industry}_percent_diff"] = stats['percent_diff']
    
    comparison_df = pd.DataFrame(comparison_data, index=employment_data_15p.index)
    comparison_df.to_csv(output_file, encoding='utf-8-sig')
    print(f"Comparison data saved to: {output_file}")

def find_scenario_files(base_dir="results/monthly_capacity_factors"):
    """
    Find CSV files for MMMU and MMMU_non_flexible scenarios.

    Looks for files with _15p (MMMU) and _non_flexible (MMMU_non_flexible) in the name.

    Parameters
    ----------
    base_dir : str
        Directory containing monthly capacity factor CSVs.

    Returns
    -------
    tuple of str
        (path_to_mmmu_csv, path_to_mmmu_non_flexible_csv)
    """
    if not os.path.exists(base_dir):
        raise FileNotFoundError(f"Directory not found: {base_dir}")

    csv_files = [f for f in os.listdir(base_dir) if f.endswith('.csv')]

    file_mmmu = None
    file_mmmu_non_flexible = None

    for csv_file in csv_files:
        if '_15p.csv' in csv_file:
            file_mmmu = os.path.join(base_dir, csv_file)
        elif '_non_flexible.csv' in csv_file:
            file_mmmu_non_flexible = os.path.join(base_dir, csv_file)

    if file_mmmu is None:
        raise FileNotFoundError("No MMMU scenario file (_15p.csv) found")
    if file_mmmu_non_flexible is None:
        raise FileNotFoundError("No MMMU_non_flexible scenario file (_non_flexible.csv) found")

    return file_mmmu, file_mmmu_non_flexible

def compare_employment_scenarios(file_mmmu=None, file_mmmu_non_flexible=None,
                                output_dir=None, config_file=None):
    """
    Compare employment between MMMU and MMMU_non_flexible scenarios.

    Parameters
    ----------
    file_mmmu : str, optional
        Path to MMMU scenario CSV (e.g. *_15p.csv).
    file_mmmu_non_flexible : str, optional
        Path to MMMU_non_flexible scenario CSV (e.g. *_non_flexible.csv).
    output_dir : str, optional
        Output directory for plots and CSV.
    config_file : str, optional
        Path to employment config YAML.
    """
    if file_mmmu is None or file_mmmu_non_flexible is None:
        try:
            file_mmmu, file_mmmu_non_flexible = find_scenario_files()
            print(f"Found MMMU file: {os.path.basename(file_mmmu)}")
            print(f"Found MMMU_non_flexible file: {os.path.basename(file_mmmu_non_flexible)}")
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return

    print("\nLoading data...")
    capacity_factors_mmmu, _ = load_csv_data(file_mmmu)
    capacity_factors_non_flexible, _ = load_csv_data(file_mmmu_non_flexible)

    if capacity_factors_mmmu.empty or capacity_factors_non_flexible.empty:
        print("Warning: No capacity factor data found in CSV files")
        return

    # In non_flexible scenario aluminum is non-dispatchable: capacity factor = 1 (add column if missing)
    capacity_factors_non_flexible = capacity_factors_non_flexible.copy()
    capacity_factors_non_flexible['Aluminum'] = 1.0

    _, config = load_employment_config(config_file)

    employment_params_mmmu = get_scenario_specific_parameters('MMMU')
    employment_params_non_flexible = get_scenario_specific_parameters('MMMU_non_flexible')

    print("Calculating employment...")
    print("MMMU scenario parameters:")
    for tech, params in employment_params_mmmu.items():
        print(f"  {params['display_name']}: {params['installed_capacity']} GW")

    print("MMMU_non_flexible scenario parameters:")
    for tech, params in employment_params_non_flexible.items():
        print(f"  {params['display_name']}: {params['installed_capacity']} GW")

    employment_data_15p = calculate_monthly_employment(capacity_factors_mmmu, employment_params_mmmu)
    employment_data_non_flexible = calculate_monthly_employment(capacity_factors_non_flexible, employment_params_non_flexible)
    
    if employment_data_15p.empty or employment_data_non_flexible.empty:
        print("Warning: No employment data calculated")
        return

    if output_dir is None:
        if config and 'output' in config:
            output_dir = config['output'].get('directory', 'results/employment_analysis')
        else:
            output_dir = "results/employment_analysis"
    
    os.makedirs(output_dir, exist_ok=True)

    plot_file = os.path.join(output_dir, "employment_scenario_comparison.pdf")
    mean_var_plot_file = os.path.join(output_dir, "mean_variance_comparison.pdf")
    csv_file = os.path.join(output_dir, "employment_scenario_comparison.csv")

    print("Calculating differences...")
    differences = calculate_scenario_differences(employment_data_15p, employment_data_non_flexible)

    print("Creating comparison plot...")
    plot_employment_comparison(employment_data_15p, employment_data_non_flexible,
                             plot_file, config, differences)

    print("Creating mean and variance comparison plot...")
    plot_mean_variance_comparison(differences, mean_var_plot_file)

    save_comparison_data(employment_data_15p, employment_data_non_flexible,
                        differences, csv_file)

    print_comparison_statistics(differences, employment_params_mmmu, employment_params_non_flexible)

def main():
    """CLI: compare employment for MMMU vs MMMU_non_flexible."""
    parser = argparse.ArgumentParser(description='Compare employment between MMMU and MMMU_non_flexible scenarios')
    parser.add_argument('--file-mmmu', '--file_15p', dest='file_mmmu', help='MMMU scenario CSV path (e.g. *_15p.csv)')
    parser.add_argument('--file-mmmu-non-flexible', '--file_non_flexible', dest='file_mmmu_non_flexible', help='MMMU_non_flexible scenario CSV path (e.g. *_non_flexible.csv)')
    parser.add_argument('-o', '--output', help='Output directory')
    parser.add_argument('-c', '--config', help='Employment config YAML path')
    
    args = parser.parse_args()
    
    try:
        compare_employment_scenarios(args.file_mmmu, args.file_mmmu_non_flexible,
                                     args.output, args.config)
    except Exception as e:
        print(f"Error: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
