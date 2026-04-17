#!/usr/bin/env python3
# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Visualization script for the cost composition of primary aluminum smelting.

It shows how the levelized cost per tonne is decomposed into cost components
for 2020 and several 2050 capacity-ratio cases.
"""

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import os

# Use Helvetica-like fonts for clear English labels
plt.rcParams['font.sans-serif'] = ['Helvetica', 'Arial', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

# Shared cost data: 2020 and 2050_0p are the same for both F and U; 15p and 100p differ by scenario
# Order: Raw materials (bottom), Other costs (above it), then the rest
# CNY/t components are fixed snapshots for publication; paired script:
# fig_6_plot_aluminum_cost_change_per_ton.py — keep in sync when updating.
CATEGORIES = ['Raw materials', 'Other costs', 'Labor', 'Fixed o&m', 'Restart', 'Depreciation', 'Retirement loss', 'Storage & Capital', 'Electricity']
# Per-category values: [raw, other, labor, fixed, restart, depr, retirement, storage, electricity]
COSTS_2020 = [8451.2, 1500, 150, 400, 0.01, 300, 0, 1, 6250]
COSTS_2050_0P = [8451.2, 1500, 150, 400, 0, 305.4, 825.7, 1, 5250.4]
COSTS_2050_15P_F = [8451.2, 1500, 160.57014, 566.9, 299.1122492, 424.2, 706.9, 258, 3090.9]
COSTS_2050_15P_U = [8451.2, 1500, 227.6, 566.9, 299.1122492, 424.2, 706.9, 258, 3090.9]
COSTS_2050_100P_F = [8451.2, 1500, 160.83, 1542.4, 795.4, 1156.812339, 0, 1030, 1631.3]
COSTS_2050_100P_U = [8451.2, 1500, 565.5, 1542.4, 795.4, 1156.812339, 0, 1030, 1631.3]

COLORS = [
    '#8B4513', '#A9A9A9', '#2E8B57', '#4169E1', '#FF4500', '#696969',
    '#FF69B4', '#9370DB', '#FFD700',
]


def create_aluminum_cost_bar_chart(scenario_type='F', output_dir='results'):
    """
    Create one stacked bar chart for scenario_type 'F' or 'U'.
    Both use the same 2020 and 2050_0p; 15p and 100p use _F or _U data respectively.
    """
    if scenario_type.upper() == 'F':
        costs_15p = COSTS_2050_15P_F
        costs_100p = COSTS_2050_100P_F
    else:
        costs_15p = COSTS_2050_15P_U
        costs_100p = COSTS_2050_100P_U

    scenario_costs = [COSTS_2020, COSTS_2050_0P, costs_15p, costs_100p]
    scenario_totals = [sum(c) for c in scenario_costs]
    scenarios = ['2020', '2050\nNo overcapacity', '2050\n30% overcapacity', '2050\nAll overcapacity']

    print(f"  [{scenario_type}] Total 2020: {scenario_totals[0]:.2f}  2050_0p: {scenario_totals[1]:.2f}  2050_15p: {scenario_totals[2]:.2f}  2050_100p: {scenario_totals[3]:.2f} CNY/tonne")

    fig, ax = plt.subplots(1, 1, figsize=(12, 11))
    x = np.arange(len(scenarios))
    width = 0.6
    bottom = np.zeros(len(scenarios))

    for i, (category, color) in enumerate(zip(CATEGORIES, COLORS)):
        category_costs = [costs[i] for costs in scenario_costs]
        bars = ax.bar(x, category_costs, width, bottom=bottom,
                      label=category, color=color, alpha=0.8)
        for j, (bar, cost) in enumerate(zip(bars, category_costs)):
            if cost > 1 and cost < 8000:
                height = bar.get_height()
                if height > 0:
                    ax.text(bar.get_x() + bar.get_width()/2,
                            bar.get_y() + height/2,
                            f'{cost:.0f}', ha='center', va='center',
                            fontsize=16, fontweight='bold', color='black')
        bottom += category_costs

    ax.set_ylabel('Levelized cost (CNY/tonne)', fontsize=20, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(scenarios, fontsize=20)
    ax.set_ylim(0, 18000)
    ax.set_yticks(np.arange(0, 18001, 2000))
    ax.set_yticklabels([f'{int(tick)}' for tick in np.arange(0, 18001, 2000)], fontsize=20)

    for i, total in enumerate(scenario_totals):
        ax.text(i, total + 400, f'Total: {total:.0f}',
                ha='center', va='bottom', fontsize=20, fontweight='bold')

    handles, labels = ax.get_legend_handles_labels()
    ax.legend(handles[::-1], labels[::-1], bbox_to_anchor=(0.5, -0.1), loc='upper center',
              ncol=4, fontsize=20, frameon=False)
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f'aluminum_cost_composition_2020_2050_stacked_bar_s_{scenario_type.upper()}.png')
    plt.savefig(output_path, dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f"  Saved: {output_path}")
    return output_path

def create_detailed_comparison_table():
    """Print a detailed comparison table of the cost breakdown (F and U scenarios)."""
    df = pd.DataFrame({
        'Category': CATEGORIES,
        '2020': COSTS_2020,
        '2050_0p': COSTS_2050_0P,
        '2050_15p_F': COSTS_2050_15P_F,
        '2050_15p_U': COSTS_2050_15P_U,
        '2050_100p_F': COSTS_2050_100P_F,
        '2050_100p_U': COSTS_2050_100P_U,
    })
    totals = {col: df[col].sum() for col in df.columns if col != 'Category'}
    print("\n=== Aluminum cost composition (CNY/tonne) ===")
    print(df.to_string(index=False, float_format='%.2f'))
    print("\nTotals:", totals)
    return df

def main():
    """CLI entry point: draw F and U scenario charts (two figures)."""
    print("Creating aluminum cost composition bar charts (F and U)...")
    create_aluminum_cost_bar_chart('F')
    create_aluminum_cost_bar_chart('U')
    create_detailed_comparison_table()
    print("\nVisualization completed!")

if __name__ == "__main__":
    main()
