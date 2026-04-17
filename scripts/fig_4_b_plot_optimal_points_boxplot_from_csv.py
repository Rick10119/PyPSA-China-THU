#!/usr/bin/env python3
# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Plot boxplots of optimal points.
Read data from optimal_points_distribution_data_latest.csv and plot boxplots.
"""

import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

def load_optimal_points_data(csv_file='results/optimal_points_analysis/optimal_points_distribution_data_latest.csv'):
    """
    Load optimal points data from CSV.
    
    Parameters:
    -----------
    csv_file : str
        Path to CSV file
        
    Returns:
    --------
    pd.DataFrame
        Loaded dataframe
    """
    try:
        df = pd.read_csv(csv_file)
        logger.info(f"Successfully loaded data: {len(df)} points")
        logger.info(f"Years: {sorted(df['year'].unique())}")
        logger.info(f"Markets: {sorted(df['market'].unique())}")
        logger.info(f"Flexibility levels: {sorted(df['flexibility'].unique())}")
        return df
    except Exception as e:
        logger.error(f"Failed to load data: {str(e)}")
        return None


def plot_combined_boxplot(df, output_dir='results/optimal_points_analysis'):
    """
    Plot combined boxplots (capacity and net value).
    
    Parameters:
    -----------
    df : pd.DataFrame
        Input data
    output_dir : str
        Output directory
    """
    set_plot_style()

    # Group by year
    years = sorted(df['year'].unique())
    
    # Prepare data
    capacity_data = []
    net_value_data = []
    year_labels = []
    
    for year in years:
        year_data = df[df['year'] == year]
        capacities = year_data['capacity'].values
        net_values = year_data['net_value'].values
        
        capacity_data.append(capacities)
        net_value_data.append(net_values)
        year_labels.append(f'{year}')
    
    # Create figure: two vertically stacked subplots
    fig, (ax1, ax2) = plt.subplots(
        2,
        1,
        figsize=(
            _mm_to_inches(FIG_WIDTH_MM),
            _mm_to_inches(FIG_HEIGHT_MM),
        ),
    )
    
    # Colors
    colors = plt.cm.viridis(np.linspace(0, 1, len(years)))
    year_colors = dict(zip(years, colors))
    
    # Top: capacity boxplot
    bp1 = ax1.boxplot(
        capacity_data,
        labels=year_labels,
        patch_artist=True,
        boxprops=dict(alpha=0.7),
        medianprops=dict(color="black", linewidth=0.9),
        showfliers=False,
    )
    
    for patch, year in zip(bp1['boxes'], years):
        patch.set_facecolor(year_colors[year])
    
    ax1.set_title(
        "Distribution of optimal smelting capacity by year",
        fontsize=TEXT_PT,
        fontweight="bold",
        pad=4,
    )
    ax1.set_ylabel("Smelting capacity (Mt/year)", fontsize=TEXT_PT, fontweight="bold")
    ax1.grid(True, alpha=0.3)
    ax1.tick_params(axis="both", which="major", labelsize=TEXT_PT)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x)}'))
    
    # Start y-axis from zero
    ax1.set_ylim(bottom=0)
    
    # Annual primary-aluminum demand reference lines (Mt/year). Same scenario as the
    # demand series used when computing excess_ratio for optimal-points exports in
    # scripts/plot_optimal_point.py (save_optimal_points_to_csv: demand in 10k t/y;
    # values here = that series / 100). Not read from CSV so the figure stays
    # comparable to the ratio definition; change both places together if the scenario updates.
    demand_by_year = {
        2030: 29.0241717,
        2040: 15.0817033,
        2050: 11.6668363,
    }
    
    for i, year in enumerate(years, 1):
        if year in demand_by_year:
            demand = demand_by_year[year]
            # Use the same color as the box
            ax1.axhline(
                y=demand,
                xmin=(i - 1) / len(years),
                xmax=i / len(years),
                color=year_colors[year],
                linestyle="--",
                linewidth=1.1,
                alpha=0.8,
            )
    
    # Compute mean capacity by year
    year_capacity_means = {}
    for i, year in enumerate(years):
        year_data = df[df['year'] == year]
        if not year_data.empty:
            year_capacity_means[year] = year_data['capacity'].mean()
    
    # Compute mean overcapacity ratio by year
    year_excess_ratio_means = {}
    for i, year in enumerate(years):
        year_data = df[df['year'] == year]
        if not year_data.empty:
            year_excess_ratio_means[year] = year_data['excess_ratio'].mean()
    
    # Connect demand values with a dashed line
    demand_years = [year for year in years if year in demand_by_year]
    if len(demand_years) > 1:
        demand_values = [demand_by_year[year] for year in demand_years]
        # Convert year to x-axis position（1, 2, 3...）
        demand_x_positions = [years.index(year) + 1 for year in demand_years]
        ax1.plot(
            demand_x_positions,
            demand_values,
            "k--",
            linewidth=1.0,
            alpha=0.6,
            label="Demand trend",
        )
    
    # Connect mean capacities with a dashed line
    if len(year_capacity_means) > 1:
        capacity_years = sorted(year_capacity_means.keys())
        capacity_values = [year_capacity_means[year] for year in capacity_years]
        # Convert year to x-axis position（1, 2, 3...）
        capacity_x_positions = [years.index(year) + 1 for year in capacity_years]
        ax1.plot(
            capacity_x_positions,
            capacity_values,
            "b--",
            linewidth=1.0,
            alpha=0.6,
            label="Average capacity trend",
        )
    
    # Annotate mean overcapacity ratio above each box
    for i, year in enumerate(years, 1):
        if year in year_excess_ratio_means:
            excess_ratio_mean = year_excess_ratio_means[year]
            # Use mean capacity to position the label
            year_data = df[df['year'] == year]
            if not year_data.empty:
                mean_capacity = year_data['capacity'].mean()
                # Add text label slightly to the right of the box
                ax1.text(
                    i + 0.2,
                    mean_capacity,
                    f"{excess_ratio_mean:.0%}",
                    ha="left",
                    va="center",
                    fontsize=TEXT_PT,
                    fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.08", facecolor="white", alpha=0.8),
                )
    
    # Bottom: net benefit boxplot
    bp2 = ax2.boxplot(
        net_value_data,
        labels=year_labels,
        patch_artist=True,
        boxprops=dict(alpha=0.7),
        medianprops=dict(color="black", linewidth=0.9),
        showfliers=False,
    )
    
    for patch, year in zip(bp2['boxes'], years):
        patch.set_facecolor(year_colors[year])
    
    ax2.set_title(
        "Distribution of optimal net benefit by year",
        fontsize=TEXT_PT,
        fontweight="bold",
        pad=4,
    )
    ax2.set_xlabel("Year", fontsize=TEXT_PT, fontweight="bold")
    ax2.set_ylabel("Net benefit (billion CNY)", fontsize=TEXT_PT, fontweight="bold")
    ax2.grid(True, alpha=0.3)
    ax2.tick_params(axis="both", which="major", labelsize=TEXT_PT)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{int(x)}'))
    
    # Start y-axis from zero
    ax2.set_ylim(bottom=0)
    
    # Compute mean net benefit by year
    year_net_value_means = {}
    for i, year in enumerate(years):
        year_data = df[df['year'] == year]
        if not year_data.empty:
            year_net_value_means[year] = year_data['net_value'].mean()
    
    # Annotate mean net benefit next to each box
    for i, year in enumerate(years, 1):
        if year in year_net_value_means:
            net_value_mean = year_net_value_means[year]
            # Use mean net benefit to position the label
            year_data = df[df['year'] == year]
            if not year_data.empty:
                mean_net_value = year_data['net_value'].mean()
                # Add text label next to the box
                ax2.text(
                    i + 0.2,
                    mean_net_value,
                    f"{net_value_mean:.0f}B",
                    ha="left",
                    va="center",
                    fontsize=TEXT_PT,
                    fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.08", facecolor="white", alpha=0.8),
                )
    
    # Add legend to the second subplot (mean net benefit only)
    if year_net_value_means:
        from matplotlib.patches import Rectangle
        legend_elements_2 = [Rectangle((0.1, 0.1), 1, 1, facecolor='white', 
                                      edgecolor='black', alpha=0.8,
                                      label='Mean optimal net benefit')]
        ax2.legend(
            handles=legend_elements_2,
            loc="upper left",
            frameon=False,
            fontsize=TEXT_PT,
        )
    
    # Legend for the first subplot
    legend_elements = []
    
    # Add trend lines to legend
    if len(demand_years) > 1:
        legend_elements.append(
            plt.Line2D(
                [0],
                [0],
                color="k",
                linestyle="--",
                linewidth=1.0,
                label="Primary aluminum demand",
            )
        )
    
    if len(year_capacity_means) > 1:
        legend_elements.append(
            plt.Line2D(
                [0],
                [0],
                color="b",
                linestyle="-.",
                linewidth=1.0,
                label="Mean optimal capacity",
            )
        )
    
    # Add mean overcapacity annotation to legend
    if year_capacity_means or year_net_value_means:
        # Use a framed legend item to represent text-box annotations
        from matplotlib.patches import Rectangle
        legend_elements.append(Rectangle((0, 0), 1, 1, facecolor='white', 
                                       edgecolor='black', alpha=0.8,
                                       label='Mean overcapacity ratio'))
    
    # Add legend to the first subplot without frame
    if legend_elements:
        ax1.legend(
            handles=legend_elements,
            loc="lower left",
            frameon=False,
            fontsize=TEXT_PT,
        )
    
    plt.tight_layout()

    # Save figure
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    plot_file = output_path / "optimal_points_combined_boxplot.pdf"
    plt.savefig(plot_file, format="pdf", bbox_inches="tight", facecolor="white")
    logger.info(f"Combined boxplot saved to: {plot_file}")
    
    return fig

def print_data_summary(df):
    """
    Print summary statistics of the data.
    
    Parameters:
    -----------
    df : pd.DataFrame
        Input dataframe
    """
    logger.info("=== Data summary ===")
    logger.info(f"Total data points: {len(df)}")
    logger.info(f"Year range: {df['year'].min()} - {df['year'].max()}")
    logger.info(f"Years included: {sorted(df['year'].unique())}")
    logger.info(f"Market types: {sorted(df['market'].unique())}")
    logger.info(f"Flexibility types: {sorted(df['flexibility'].unique())}")
    
    logger.info("\n=== Statistics by year ===")
    for year in sorted(df['year'].unique()):
        year_data = df[df['year'] == year]
        logger.info(f"Year {year}:")
        logger.info(f"  Number of points: {len(year_data)}")
        logger.info(
            f"  Capacity range: "
            f"{year_data['capacity'].min():.1f} - {year_data['capacity'].max():.1f} Mt/year"
        )
        logger.info(
            f"  Net benefit range: "
            f"{year_data['net_value'].min():.2f} - {year_data['net_value'].max():.2f} Billion CNY"
        )
        logger.info(
            f"  Overcapacity ratio range: "
            f"{year_data['excess_ratio'].min():.1%} - {year_data['excess_ratio'].max():.1%}"
        )

def main():
    """
    Main entry point.
    """
    # Data file path
    csv_file = 'results/optimal_points_analysis/optimal_points_distribution_data_latest.csv'
    
    # Load data
    df = load_optimal_points_data(csv_file)
    if df is None:
        logger.error("Failed to load data; exiting.")
        return
    
    # Print summary
    print_data_summary(df)
    
    # Plot combined boxplots (capacity and net benefit)
    logger.info("Start plotting combined boxplots...")
    plot_combined_boxplot(df)
    
    logger.info("Boxplots finished.")

if __name__ == "__main__":
    main()
