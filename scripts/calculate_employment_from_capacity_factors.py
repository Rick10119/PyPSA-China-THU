# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Calculate smelter based on average capacity factor data、coal、gasScript for monthly employment numbers by industry
Calculation formula：Number of people employed = average capacity factor × Installed capacity × Number of employees per unit installed capacity
"""

import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import os
import argparse
import yaml

def set_plot_style():
    """
    Set drawing style
    """
    # Set English font
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
    Load capacity factor data from CSV file，All use averaging factors
    
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
        raise FileNotFoundError(f"CSV file not found: {csv_file}")
    
    # Read CSV file
    df = pd.read_csv(csv_file, index_col='Month')
    
    # Separate average capacity factor and load factor data
    avg_capacity_cols = [col for col in df.columns if 'Capacity_Factor_Avg' in col]
    load_cols = [col for col in df.columns if 'Load_Factor' in col]
    
    # Prefer average capacity factor data
    if avg_capacity_cols:
        # Using average capacity factor data
        capacity_factors = df[avg_capacity_cols]
        capacity_factors.columns = [col.replace('_Capacity_Factor_Avg', '') for col in capacity_factors.columns]
        print("Using monthly average capacity factor data for employment calculation")
        
    else:
        # Compatible with older formats：Only Capacity_Factor column
        capacity_cols = [col for col in df.columns if 'Capacity_Factor' in col and 'Max' not in col and 'Avg' not in col]
        capacity_factors = df[capacity_cols] if capacity_cols else pd.DataFrame()
        capacity_factors.columns = [col.replace('_Capacity_Factor', '') for col in capacity_factors.columns]
        print("Using legacy capacity factor format for employment calculation")
    
    load_factors = df[load_cols] if load_cols else pd.DataFrame()
    # Rename the load factor column
    load_factors.columns = [col.replace('_Load_Factor', '') for col in load_factors.columns]
    
    return capacity_factors, load_factors

def load_employment_config(config_file=None):
    """
    Load employment parameter configuration file
    
    Parameters:
    -----------
    config_file : str, optional
        Configuration file path，Default is employment_config.yaml
    
    Returns:
    --------
    dict
        Dictionary containing parameters for various industries
    """
    if config_file is None:
        config_file = os.path.join(os.path.dirname(__file__), 'employment_config.yaml')
    
    if not os.path.exists(config_file):
        print(f"Warning: Config file {config_file} not found, using default parameters")
        return get_default_employment_parameters()
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        
        # Convert configuration format
        employment_params = {}
        for tech, params in config['industries'].items():
            # Handle different unit systems
            if 'employment_per_GW' in params:
                # New unit system：GWand thousands of people/GW
                employment_params[tech] = {
                    'installed_capacity': params['installed_capacity'],  # Use GW directly
                    'employment_per_gw': params['employment_per_GW'],     # Directly use thousands of people/GW
                    'display_name': params['display_name'],
                    'unit_system': 'GW'
                }
            else:
                # old unit system：MWKazuto/MW
                employment_params[tech] = {
                    'installed_capacity': params['installed_capacity'],
                    'employment_per_mw': params['employment_per_mw'],
                    'display_name': params['display_name'],
                    'unit_system': 'MW'
                }
        
        return employment_params, config
    except Exception as e:
        print(f"Warning: Failed to read config file: {e}, using default parameters")
        return get_default_employment_parameters(), {}

def get_default_employment_parameters():
    """
    Get default parameters for each industry
    
    Returns:
    --------
    dict
        Dictionary containing parameters for various industries
    """
    # Default parameter value
    # unit：Installed capacity(MW)，Number of employees in the unit(people/MW)
    employment_params = {
        'Aluminum': {
            'installed_capacity': 18.8,  # MW - Aluminum smelting plant installed capacity
            'employment_per_mw': 15.4,      # people/MW - Number of jobs per MW of installed capacity
            'display_name': 'Aluminum Smelter'
        },
        'Coal': {
            'installed_capacity': 328.0,  # MW - Coal power installed capacity
            'employment_per_mw': 0.8,      # people/MW - Number of jobs per MW of installed capacity
            'display_name': 'Coal Power'
        },
        'Gas': {
            'installed_capacity': 100.80,  # MW - Natural gas power generation installed capacity
            'employment_per_mw': 0.2,      # people/MW - Number of jobs per MW of installed capacity
            'display_name': 'Gas Power'
        }
    }
    
    return employment_params

def calculate_monthly_employment(capacity_factors, employment_params):
    """
    Calculation of monthly employment based on average capacity factor
    
    Parameters:
    -----------
    capacity_factors : pd.DataFrame
        Average capacity factor data，action month，listed as technology type
    employment_params : dict
        Installed capacity and unit employment parameters of various industries
    
    Returns:
    --------
    pd.DataFrame
        Month-by-month employment data
    """
    employment_data = {}
    
    for tech, params in employment_params.items():
        if tech in capacity_factors.columns:
            # Calculate employment：average capacity factor × Installed capacity × Number of employees in the unit
            if params.get('unit_system') == 'GW':
                # Use the GW unit system
                monthly_employment = (capacity_factors[tech] * 
                                    params['installed_capacity'] * 
                                    params['employment_per_gw'])
            else:
                # Use the MW unit system
                monthly_employment = (capacity_factors[tech] * 
                                    params['installed_capacity'] * 
                                    params['employment_per_mw'])
            employment_data[params['display_name']] = monthly_employment
        else:
            print(f"Warning: No capacity factor data found for {tech}")
    
    return pd.DataFrame(employment_data, index=capacity_factors.index)

def plot_employment_trends(employment_data, output_file=None, title_suffix="", config=None):
    """
    Chart employment trends
    
    Parameters:
    -----------
    employment_data : pd.DataFrame
        Employment data
    output_file : str, optional
        Output image file path
    title_suffix : str, optional
        Chart title suffix
    config : dict, optional
        Configuration dictionary，Contains settings such as color
    """
    if employment_data.empty:
        print("Warning: No employment data to plot")
        return
    
    # Set drawing style
    set_plot_style()
    
    # Get chart settings from configuration
    if config and 'plot' in config:
        plot_config = config['plot']
        figsize = plot_config.get('figure_size', [12, 8])
        dpi = plot_config.get('dpi', 150)
        font_size = plot_config.get('font_size', 18)
        legend_font_size = plot_config.get('legend_font_size', 20)
        title_font_size = plot_config.get('title_font_size', 24)
        axis_font_size = plot_config.get('axis_font_size', 24)
    else:
        figsize = [12, 8]
        dpi = 150
        font_size = 18
        legend_font_size = 20
        title_font_size = 24
        axis_font_size = 24
    
    # Create chart
    fig, ax = plt.subplots(1, 1, figsize=figsize)
    
    # Get color settings from configuration
    if config and 'colors' in config:
        colors = config['colors']
    else:
        # Default color
        colors = {
            'Aluminum Smelter': '#FF69B4',    # Hot pink
            'Coal Power': '#000000',          # Black
            'Gas Power': '#FF0000'            # Red
        }
    
    # Chart employment trends by industry（Overlay area chart）
    # Reorder columns，Aluminum Smelterput on top
    column_order = []
    if 'Aluminum Smelter' in employment_data.columns:
        column_order.append('Aluminum Smelter')
    for col in employment_data.columns:
        if col != 'Aluminum Smelter':
            column_order.append(col)
    
    # Rearrange data in new order
    employment_data_ordered = employment_data[column_order]
    
    # Calculate cumulative values ​​for overlay area charts
    cumulative_data = employment_data_ordered.cumsum(axis=1)
    
    # Draw an overlay area chart
    for i, industry in enumerate(employment_data_ordered.columns):
        months = employment_data_ordered.index
        if i == 0:
            # first industry：Start drawing from 0
            values = cumulative_data[industry].values
            ax.fill_between(months, 0, values, color=colors.get(industry, '#808080'), 
                           alpha=0.7, label=industry)
        else:
            # Follow-up industries：Plot starting from the cumulative value of the previous industry
            prev_industry = employment_data_ordered.columns[i-1]
            prev_values = cumulative_data[prev_industry].values
            values = cumulative_data[industry].values
            ax.fill_between(months, prev_values, values, color=colors.get(industry, '#808080'), 
                           alpha=0.7, label=industry)
    
    # Add border lines to make graphics clearer
    for industry in employment_data_ordered.columns:
        months = employment_data_ordered.index
        values = employment_data_ordered[industry].values
        color = colors.get(industry, '#808080')
        ax.plot(months, values, color=color, linewidth=1, alpha=0.8)
    
    # Set chart properties
    ax.set_ylabel('Cumulative Employment (thousands)', fontsize=axis_font_size)
    ax.set_xlabel('Month', fontsize=axis_font_size)
    ax.set_title(f'Monthly Employment by Industry (Stacked Area){title_suffix}', fontsize=title_font_size)
    ax.set_xlim(1.0, 12.0)
    ax.set_xticks(range(1, 13))
    ax.set_xticklabels(['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], fontsize=font_size)
    ax.tick_params(axis='y', labelsize=font_size)
    ax.grid(True, alpha=0.3)
    ax.legend(loc='best', fontsize=legend_font_size)
    
    # Adjust layout
    plt.tight_layout()
    
    # Save chart
    if output_file is None:
        output_file = f"employment_trends{title_suffix.replace(' ', '_')}.png"
    
    fig.savefig(output_file, dpi=dpi, bbox_inches='tight')
    plt.show()
    plt.close()
    
    print(f"Employment trend plot saved to: {output_file}")

def save_employment_data(employment_data, output_file=None, title_suffix=""):
    """
    Save employment data to CSV file
    
    Parameters:
    -----------
    employment_data : pd.DataFrame
        Employment data
    output_file : str, optional
        Output CSV file path
    title_suffix : str, optional
        File name suffix
    """
    if output_file is None:
        output_file = f"monthly_employment{title_suffix.replace(' ', '_')}.csv"
    
    employment_data.to_csv(output_file, encoding='utf-8-sig')
    print(f"Employment data saved to: {output_file}")

def print_employment_statistics(employment_data, employment_params):
    """
    Print employment statistics
    
    Parameters:
    -----------
    employment_data : pd.DataFrame
        Employment data
    employment_params : dict
        Parameters of various industries
    """
    print(f"\nMonthly Employment Statistics by Industry (Based on Average Capacity Factors)")
    print("=" * 80)
    
    for industry in employment_data.columns:
        data = employment_data[industry]
        avg_employment = data.mean()
        max_employment = data.max()
        min_employment = data.min()
        total_annual = data.sum()
        
        # Unified display of thousands of units
        print(f"{industry:15s}: Avg={avg_employment:.1f}k, Max={max_employment:.1f}k, "
              f"Min={min_employment:.1f}k, Annual Total={total_annual:.1f}k")
    
    print(f"\nIndustry Parameters")
    print("=" * 40)
    for tech, params in employment_params.items():
        if tech in ['Aluminum', 'Coal', 'Gas']:
            unit_system = params.get('unit_system', 'MW')
            if unit_system == 'GW':
                # Show GW units
                print(f"{params['display_name']:15s}: Capacity={params['installed_capacity']:.1f}GW, "
                      f"Employment={params['employment_per_gw']:.1f}k/GW")
            else:
                # Display MW unit
                print(f"{params['display_name']:15s}: Capacity={params['installed_capacity']:.0f}MW, "
                      f"Employment={params['employment_per_mw']:.1f}/MW")

def calculate_employment_from_csv(csv_file, output_dir=None, title_suffix="", config_file=None):
    """
    Main function to calculate employment based on average capacity factor from CSV file
    
    Parameters:
    -----------
    csv_file : str
        CSVfile path
    output_dir : str, optional
        Output directory
    title_suffix : str, optional
        Title suffix
    config_file : str, optional
        Configuration file path
    """
    # Load data
    capacity_factors, load_factors = load_csv_data(csv_file)
    
    if capacity_factors.empty:
        print("Warning: No capacity factor data found in CSV file")
        return
    
    # Get employment parameters and configuration
    employment_params, config = load_employment_config(config_file)
    
    # Calculate employment
    employment_data = calculate_monthly_employment(capacity_factors, employment_params)
    
    if employment_data.empty:
        print("Warning: No employment data calculated")
        return
    
    # Set output directory
    if output_dir is None:
        if config and 'output' in config:
            output_dir = config['output'].get('directory', 'results/employment_analysis')
        else:
            output_dir = "results/employment_analysis"
    
    os.makedirs(output_dir, exist_ok=True)
    
    # Generate output file name
    base_name = os.path.splitext(os.path.basename(csv_file))[0]
    plot_file = os.path.join(output_dir, f"{base_name}_employment_plot.png")
    csv_file_out = os.path.join(output_dir, f"{base_name}_employment_data.csv")
    
    # Draw charts
    plot_employment_trends(employment_data, plot_file, title_suffix, config)
    
    # save data
    save_employment_data(employment_data, csv_file_out, title_suffix)
    
    # Print statistics
    print_employment_statistics(employment_data, employment_params)

def main():
    """
    main function，Handling command line arguments
    """
    parser = argparse.ArgumentParser(description='Calculate employment from average capacity factor data')
    parser.add_argument('csv_file', help='CSV file path')
    parser.add_argument('-o', '--output', help='Output directory')
    parser.add_argument('-t', '--title', default='', help='Title suffix')
    parser.add_argument('-c', '--config', help='Config file path')
    
    args = parser.parse_args()
    
    try:
        calculate_employment_from_csv(args.csv_file, args.output, args.title, args.config)
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
                print(f"Found {len(csv_files)} CSV files:")
                for csv_file in csv_files:
                    print(f"  - {csv_file}")
                
                print("\nStarting file processing...")
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
                    
                    calculate_employment_from_csv(csv_path, title_suffix=title_suffix)
            else:
                print(f"No CSV files found in {csv_dir} directory")
                print("Please run plot_capacity_factors.py first to generate CSV files")
        else:
            print(f"Directory {csv_dir} does not exist")
            print("Please run plot_capacity_factors.py first to generate CSV files")
    else:
        exit(main())
