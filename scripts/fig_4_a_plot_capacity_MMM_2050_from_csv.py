#!/usr/bin/env python3
# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
# -*- coding: utf-8 -*-
"""
Simplified plotting script for the MMMU-2050 scenario cost analysis.

The script reads the pre-processed file `mmmu_2050_M_detailed_data.csv` and
produces a figure with:
  - x-axis: aluminum smelting capacity (5p–100p, converted to Mt/year)
  - left y-axis: cost savings / increases (billion CNY)

It displays electricity-system cost savings, aluminum operational cost changes,
and net cost savings.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import logging

# Publication: sans-serif (Helvetica / Arial), 6 pt, figure 88×88 mm, PDF output
TEXT_PT = 6
FIG_WIDTH_MM = 88
FIG_HEIGHT_MM = 88


def _mm_to_inches(mm: float) -> float:
    return mm / 25.4


def set_plot_style():
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


# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def load_detailed_data(csv_path):
    """
    Load the detailed MMMU-2050 data from CSV.

    Parameters
    ----------
    csv_path : str or Path
        Path to the CSV file.

    Returns
    -------
    pd.DataFrame
        Data frame with scenario metrics.
    """
    try:
        df = pd.read_csv(csv_path)
        logger.info(f"Successfully loaded data file: {csv_path}")
        logger.info(f"Data shape: {df.shape}")
        return df
    except Exception as e:
        logger.error(f"Failed to load data file: {str(e)}")
        return None

def plot_mmm_2050_from_csv(csv_path, output_dir=None):
    """
    Plot MMMU-2050 cost and emissions metrics from a prepared CSV file.

    Parameters
    ----------
    csv_path : str or Path
        Path to the CSV file created by the analysis workflow.
    output_dir : str or Path, optional
        Output directory for the figure (defaults to the CSV parent directory).
    """
    # Load data
    df = load_detailed_data(csv_path)
    if df is None:
        return

    set_plot_style()

    # Configure output directory
    if output_dir is None:
        output_dir = Path(csv_path).parent
    else:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create figure
    fig, ax = plt.subplots(
        1,
        1,
        figsize=(
            _mm_to_inches(FIG_WIDTH_MM),
            _mm_to_inches(FIG_HEIGHT_MM),
        ),
    )
    
    # Prepare data arrays
    x = df['Capacity_Value_Mt'].values
    power_savings = df['Power_Cost_Changes_Billion_CNY'].values
    aluminum_changes = df['Aluminum_Cost_Changes_Billion_CNY'].values
    net_savings = df['Net_Cost_Savings_Billion_CNY'].values
    
    # Set bar width
    bar_width = 150
    
    # Use slightly offset x-positions so electricity and aluminum bars do not overlap
    x_power = [pos - bar_width/3 for pos in x]      # electricity-system costs shifted left
    x_aluminum = [pos + bar_width/3 for pos in x]   # aluminum operational costs shifted further right
    
    # Plot electricity-system cost savings (bottom bars)
    bars1 = ax.bar(x_power, power_savings, bar_width*0.8, color='#1f77b4', alpha=0.8, 
                   label='Power system cost savings')
    
    # electrolytic aluminum cost：from power_savings Location“down”deduction（draw negative direction），
    # This way the end points after stacking will align with the net savings net_savings（When the black line value is correct）。
    breakdown_cols = [
        # warm monochrome palette (colorbrewer-like), distinct from power-system blue
        ('Aluminum_Maintenance_Billion_CNY', '#b35806', 'Aluminum smelter: maintenance cost increase'),
        ('Aluminum_Labor_Billion_CNY', '#f1a340', 'Aluminum smelter: labor cost increase'),
        ('Aluminum_Restart_Billion_CNY', '#fee0b6', 'Aluminum smelter: restart cost increase'),
    ]
    has_breakdown = all(col in df.columns for col, _, _ in breakdown_cols)

    if has_breakdown:
        bottom = np.asarray(power_savings, dtype=float)
        for col, color, label in breakdown_cols:
            comp = df[col].values
            comp = np.asarray(comp, dtype=float)
            # Costs should reduce net savings: always draw downward
            comp_down = -np.abs(comp)
            ax.bar(
                x_aluminum,
                comp_down,
                bar_width * 0.8,
                bottom=bottom,
                color=color,
                alpha=0.85,
                edgecolor="white",
                linewidth=0.6,
                label=label,
            )
            bottom = bottom + comp_down
    else:
        aluminum_down = -np.abs(np.asarray(aluminum_changes, dtype=float))
        ax.bar(
            x_aluminum,
            aluminum_down,
            bar_width * 0.8,
            bottom=power_savings,
            color='#ff7f0e',
            alpha=0.8,
            edgecolor="white",
            linewidth=0.6,
            label='Aluminum smelter: Operation Cost Increase',
        )
    
    # Plot net cost savings curve (black line at original x positions)
    ax.plot(
        x,
        net_savings,
        "k-",
        linewidth=1.2,
        label="Net cost savings",
        marker="o",
        markersize=3.5,
        zorder=20,
    )
    
    # Find the point with maximum net savings
    max_saving_index = np.argmax(net_savings)
    max_saving_value = net_savings[max_saving_index]
    max_saving_capacity = x[max_saving_index]
    
    # Mark the maximum net-savings point with a star
    ax.plot(
        max_saving_capacity,
        max_saving_value,
        "r*",
        markersize=8,
        label=f"Highest net cost savings: {max_saving_value:.0f}B CNY",
        zorder=30,
    )
    
    # Add numeric label for the maximum net-savings point
    ax.annotate(
        f"{max_saving_value:.0f}B",
        xy=(max_saving_capacity, max_saving_value),
        xytext=(0, 8),
        textcoords="offset points",
        ha="center",
        va="bottom",
        fontsize=TEXT_PT,
        weight="bold",
        color="red",
        bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.8),
    )
    
    # Axis labels
    ax.set_xlabel("Aluminum smelting capacity (Mt/Year)", fontsize=TEXT_PT)
    ax.set_ylabel("Cost savings/increase (Billion CNY)", fontsize=TEXT_PT, color="blue")
    
    # Add light grid
    ax.grid(True, alpha=0.3, axis='y')
    
    # Configure x-axis ticks and labels
    ax.set_xticks(x)
    ax.set_xticklabels([f"{cap / 100:.0f}" for cap in x], fontsize=TEXT_PT)
    
    # Configure left y-axis tick labels
    y_ticks = ax.get_yticks()
    y_tick_labels = [f'{tick:.0f}' for tick in y_ticks]
    ax.set_yticks(y_ticks)
    ax.set_yticklabels(y_tick_labels, fontsize=TEXT_PT, color="blue")
    
    # Build legend dynamically from plotted artists so that aluminum components appear separately
    handles, labels = ax.get_legend_handles_labels()
    ax.legend(
        handles=handles,
        labels=labels,
        loc="lower left",
        bbox_to_anchor=(0.02, -0.02),
        bbox_transform=ax.transAxes,
        fontsize=TEXT_PT,
        frameon=False,
    )
    
    plt.tight_layout()
    
    # Save figure
    # Derive a stable output name from the CSV stem, e.g.
    #   mmmu_2050_M_detailed_data.csv -> mmmu_2050_analysis.pdf
    #   mmmf_2050_M_detailed_data.csv -> mmmf_2050_analysis.pdf
    stem = Path(csv_path).stem
    scenario_tag = stem.replace("_M_detailed_data", "").replace("_detailed_data", "")
    plot_file = output_dir / f"{scenario_tag}_analysis.pdf"
    plt.savefig(plot_file, format="pdf", bbox_inches="tight", facecolor="white")
    logger.info(f"Scenario analysis figure saved to: {plot_file}")
    
    
    return fig, ax

def main():
    """CLI entry point."""
    # Plot MMMU and MMMF if corresponding CSV files exist (each saved under its own folder)
    candidate_csv_paths = [
        Path("results/mmmu_2050_analysis/mmmu_2050_M_detailed_data.csv"),
        Path("results/mmmf_2050_analysis/mmmf_2050_M_detailed_data.csv"),
        # Backward-compatible fallback to the old naming convention
        Path("results/mmm_2050_analysis/mmm_2050_M_detailed_data.csv"),
    ]

    csv_paths = [p for p in candidate_csv_paths if p.exists()]
    if not csv_paths:
        logger.error("No detailed CSV file found for plotting.")
        logger.info(
            "Expected one of: "
            "`results/mmmu_2050_analysis/mmmu_2050_M_detailed_data.csv`, "
            "`results/mmmf_2050_analysis/mmmf_2050_M_detailed_data.csv`, "
            "or legacy `results/mmm_2050_analysis/mmm_2050_M_detailed_data.csv`."
        )
        return

    logger.info("Start plotting MMM*-2050 scenario analysis figure(s).")
    for csv_path in csv_paths:
        logger.info(f"Data file: {csv_path}")
        try:
            plot_mmm_2050_from_csv(csv_path)
            logger.info("Plotting complete.")
        except Exception as e:
            logger.error(f"Error while plotting from {csv_path}: {str(e)}")
            raise

if __name__ == "__main__":
    main()
