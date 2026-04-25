# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Script to read monthly capacity factor data from CSV file and graph it
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import os
import argparse

def set_plot_style():
    """
    Set drawing style
    """
    # Set the font to Helvetica
    plt.rcParams['font.sans-serif'] = ['Helvetica', 'Arial', 'sans-serif']
    plt.rcParams['axes.unicode_minus'] = False
    
    plt.style.use(['classic', 'seaborn-v0_8-whitegrid',
                   {'axes.grid': False, 'grid.linestyle': '--', 'grid.color': u'0.6',
                    'hatch.color': 'white',
                    'patch.linewidth': 0.5,
                    'font.size': 18,
                    'legend.fontsize': 'large',
                    'lines.linewidth': 1.5,
                    'pdf.fonttype': 42,
                    }])

def load_csv_data(csv_file):
    """
    Load data from CSV file
    
    Parameters:
    -----------
    csv_file : str
        CSVfile path
    
    Returns:
    --------
    tuple
        (capacity_factors, load_factors) Two DataFrames
    """
    if not os.path.exists(csv_file):
        raise FileNotFoundError(f"CSVFile does not exist: {csv_file}")
    
    # Read CSV file
    df = pd.read_csv(csv_file, index_col='Month')
    
    # Separate capacity factor and load factor data
    capacity_cols = [col for col in df.columns if 'Capacity_Factor' in col]
    load_cols = [col for col in df.columns if 'Load_Factor' in col]
    
    capacity_factors = df[capacity_cols] if capacity_cols else pd.DataFrame()
    load_factors = df[load_cols] if load_cols else pd.DataFrame()
    
    # Rename columns，remove suffix
    capacity_factors.columns = [col.replace('_Capacity_Factor', '') for col in capacity_factors.columns]
    load_factors.columns = [col.replace('_Load_Factor', '') for col in load_factors.columns]
    
    return capacity_factors, load_factors

def plot_capacity_factors_from_csv(csv_file, output_file=None, title_suffix=""):
    """
    Plot capacity factor chart from CSV file
    
    Parameters:
    -----------
    csv_file : str
        CSVfile path
    output_file : str, optional
        Output image file path
    title_suffix : str, optional
        Chart title suffix
    """
    # Load data
    capacity_factors, load_factors = load_csv_data(csv_file)
    
    if capacity_factors.empty and load_factors.empty:
        print("Warning: No capacity factor or load factor data found in CSV file")
        return
    
    # Set drawing style
    set_plot_style()
    
    # Create two subgraphs, upper and lower
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9))
    
    # Define color
    colors = {
        'Hydro': '#0000FF',      # Blue
        'Nuclear': '#800080',    # Purple
        'Coal': '#000000',       # Black
        'Gas': '#FF0000',        # Red
        'Wind': '#00FF00',       # Green
        'Solar': '#FFD700',      # Gold
        'Aluminum': '#FF69B4',   # Hot pink
        'Other': '#808080'       # Gray
    }
    
    load_colors = {
        'Electricity Load': '#1f77b4',    # Blue
        'Heating Load': '#ff7f0e',        # Orange
        'Aluminum Load': '#2ca02c'        # Green
    }
    
    # Define display label mapping
    display_labels = {
        'Heating Load': 'Heating demand',
        'Aluminum Load': 'Aluminum smelter',
        'Electricity Load': 'Electricity load'
    }
    
    # Upper sub-picture：electrical load、heating loadand aluminum
    load_types_upper = ['Electricity Load', 'Heating Load']
    for load_type in load_factors.columns:
        if load_type in load_types_upper:
            months = load_factors.index
            values = load_factors[load_type].values
            color = load_colors.get(load_type, '#000000')
            # Use display labels
            display_label = display_labels.get(load_type, load_type)
            ax1.plot(months, values, 's--', color=color, 
                    linewidth=4, markersize=6, label=display_label)
    
    # Add aluminum capacity factor to the upper subplot
    if 'Aluminum' in capacity_factors.columns:
        months = capacity_factors.index
        values = capacity_factors['Aluminum'].values
        color = colors.get('Aluminum', '#000000')
        ax1.plot(months, values, 'o-', color=color, 
                linewidth=4, markersize=6, label='Aluminum smelter')
    
    # Next sub-picture：Hydro, Coal, Gas, Wind, Solar
    tech_types_lower = ['Hydro', 'Coal', 'Gas', 'Wind', 'Solar']
    for tech in capacity_factors.columns:
        if tech in tech_types_lower:
            months = capacity_factors.index
            values = capacity_factors[tech].values
            color = colors.get(tech, '#000000')
            ax2.plot(months, values, 'o-', color=color, 
                    linewidth=4, markersize=6, label=tech)
    
    # Set the upper subgraph properties
    ax1.set_ylabel('Capacity Factor', fontsize=30)
    ax1.set_title(f'Monthly Load & Smelter Capacity Factors', 
                 fontsize=30)
    ax1.set_xlim(1.0, 12.0)  # Extend x-axis range，Avoid lines being obscured by axes
    ax1.set_ylim(-0.005, 1.005)      # Adjust the y-axis range to 0-0.5
    ax1.set_xticks(range(1, 13))
    ax1.set_xticklabels(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                         'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], fontsize=30)
    ax1.tick_params(axis='y', labelsize=30)  # Set the y-axis tick size
    ax1.grid(True, alpha=0.3)
    
    # Set the subgraph properties
    ax2.set_ylabel('Capacity Factor', fontsize=30)
    ax2.set_xlabel('Month', fontsize=30)
    ax2.set_title(f'Monthly Generation Capacity Factors', 
                 fontsize=30)
    ax2.set_xlim(1.0, 12.0)  # Extend x-axis range，Avoid lines being obscured by axes
    ax2.set_ylim(-0.0025, 0.8025)      # Adjust the y-axis range to 0-0.5
    ax2.set_xticks(range(1, 13))
    ax2.set_xticklabels(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                         'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], fontsize=30)
    ax2.tick_params(axis='y', labelsize=30)  # Set the y-axis tick size
    ax2.grid(True, alpha=0.3)
    
    # Put the legend of each subplot outside
    ax1.legend(loc='center left', bbox_to_anchor=(1.04, 0.5), ncol=1, fontsize=30, borderaxespad=0.)
    # Set legend order: hydro, wind, solar, gas, coal
    legend_order = ['Hydro', 'Wind', 'Solar', 'Gas', 'Coal']
    handles, labels = ax2.get_legend_handles_labels()
    label_handle_map = dict(zip(labels, handles))
    ordered_handles = [label_handle_map[l] for l in legend_order if l in label_handle_map]
    ordered_labels = [l for l in legend_order if l in label_handle_map]
    ax2.legend(ordered_handles, ordered_labels, loc='center left', bbox_to_anchor=(1.04, 0.5), ncol=1, fontsize=30, borderaxespad=0.)
    
    # Adjust sub-picture spacing
    plt.tight_layout()
    
    # Further adjust sub-picture spacing，Leave more space for the legend on the right
    plt.subplots_adjust(right=2)
    
    # Save chart
    if output_file is None:
        output_file = csv_file.replace('.csv', '_plot.png')
    
    fig.savefig(output_file, dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"Plot saved to: {output_file}")
    
    # Print statistics
    print(f"\nTop subplot - Load factor and Aluminum capacity factor monthly statistics{title_suffix}")
    print("=" * 60)
    
    # Show load factor statistics
    if not load_factors.empty:
        for load_type in load_factors.columns:
            if load_type in load_types_upper:  # Only the load types of the upper subgraph are displayed.
                data = load_factors[load_type]
                avg_load = data.mean()
                max_load = data.max()
                min_load = data.min()
                # Use display labels
                display_label = display_labels.get(load_type, load_type)
                print(f"{display_label:15s}: avg={avg_load:.3f}, max={max_load:.3f}, min={min_load:.3f}")
    
    # Display Aluminum capacity factor statistics
    if 'Aluminum' in capacity_factors.columns:
        data = capacity_factors['Aluminum']
        avg_cf = data.mean()
        max_cf = data.max()
        min_cf = data.min()
        print(f"{'Aluminum':15s}: avg={avg_cf:.3f}, max={max_cf:.3f}, min={min_cf:.3f}")
    
    print(f"\nBottom subplot - Generator capacity factor monthly statistics (Hydro, Coal, Gas, Wind, Solar){title_suffix}")
    print("=" * 70)
    for tech in capacity_factors.columns:
        if tech in tech_types_lower:  # Technology to display only lower sub-images
            data = capacity_factors[tech]
            avg_cf = data.mean()
            max_cf = data.max()
            min_cf = data.min()
            print(f"{tech:15s}: avg={avg_cf:.3f}, max={max_cf:.3f}, min={min_cf:.3f}")

def main():
    """
    main function，Handling command line arguments
    """
    parser = argparse.ArgumentParser(description='Plot capacity factor chart from CSV file')
    parser.add_argument('csv_file', help='CSVfile path')
    parser.add_argument('-o', '--output', help='Output image file path')
    parser.add_argument('-t', '--title', default='', help='Chart title suffix')
    
    args = parser.parse_args()
    
    try:
        plot_capacity_factors_from_csv(args.csv_file, args.output, args.title)
    except Exception as e:
        print(f"Error: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    # If you run the script directly，Automatically find and process all available CSV files
    if len(os.sys.argv) == 1:
        # Find all available CSV files
        csv_dir = "results/monthly_capacity_factors"
        if os.path.exists(csv_dir):
            csv_files = [f for f in os.listdir(csv_dir) if f.endswith('.csv')]
            csv_files.sort()  # Sort by file name
            
            if csv_files:
                print(f"Found {len(csv_files)} CSV file(s):")
                for csv_file in csv_files:
                    print(f"  - {csv_file}")
                
                print("\nStarting to process files...")
                for csv_file in csv_files:
                    csv_path = os.path.join(csv_dir, csv_file)
                    print(f"\nProcessing file: {csv_file}")
                    
                    # Extract information from file name as title suffix
                    base_name = csv_file.replace('.csv', '')
                    if '_' in base_name:
                        parts = base_name.split('_')
                        if len(parts) >= 3:
                            year = parts[2] if parts[2].isdigit() else ""
                            province = parts[3] if len(parts) > 3 else ""
                            title_suffix = f" - {province} {year}" if province and year else f" - {base_name}"
                        else:
                            title_suffix = f" - {base_name}"
                    else:
                        title_suffix = f" - {base_name}"
                    
                    plot_capacity_factors_from_csv(csv_path, title_suffix=title_suffix)
            else:
                print(f"No CSV files found in directory {csv_dir}")
                print("Please run plot_capacity_factors.py first to generate CSV files")
        else:
            print(f"Directory {csv_dir} does not exist")
            print("Please run plot_capacity_factors.py first to generate CSV files")
    else:
        exit(main())
