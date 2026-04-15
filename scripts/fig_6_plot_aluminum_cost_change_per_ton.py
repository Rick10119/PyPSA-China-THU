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

# Publication: sans-serif (Helvetica / Arial), 6 pt body text, 5 pt on-figure number labels, width 88 mm
TEXT_PT = 6
ANNOTATION_PT = 5  # numeric labels on bars and total markers
# Do not print on-figure labels when |value| is below this (or above LABEL_ABS_MAX).
LABEL_ABS_MIN = 150
LABEL_ABS_MAX = 8000
FIG_WIDTH_MM = 88
# Original layout was 12×9 in; keep the same aspect for height
FIG_HEIGHT_MM = FIG_WIDTH_MM * 9 / 12


def _mm_to_inches(mm: float) -> float:
    return mm / 25.4


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
    }
)

# Shared cost data: 2020 and 2050_0p are the same for both F and U; 15p and 100p differ by scenario
# We only plot categories with changes: Labor, Fixed o&m, Restart, Depreciation,
# Retirement loss, Storage & Capital, Electricity.
CATEGORIES = ['Labor', 'Fixed o&m', 'Restart', 'Depreciation', 'Retirement loss', 'Storage & capital', 'Electricity']
# Per-category values for the listed categories (order must match CATEGORIES)
COS_IDX = {
    'Labor': 1,
    'Fixed o&m': 2,
    'Restart': 3,
    'Depreciation': 4,
    'Retirement loss': 5,
    'Storage & capital': 6,
    'Electricity': 7,
}
_COSTS_2020_FULL = [8451.2, 150, 400, 0.01, 300, 0, 1, 6250, 1500]
_COSTS_2050_0P_FULL = [8451.2, 150, 400, 0, 305.4, 825.7, 1, 5250.4, 1500]
_COSTS_2050_15P_F_FULL = [8451.2, 160.57014, 566.9, 299.1122492, 424.2, 706.9, 258, 3090.9, 1500]
_COSTS_2050_15P_U_FULL = [8451.2, 227.6, 566.9, 299.1122492, 424.2, 706.9, 258, 3090.9, 1500]
_COSTS_2050_100P_F_FULL = [8451.2, 160.83, 1542.4, 795.4, 1156.812339, 0, 1030, 1631.3, 1500]
_COSTS_2050_100P_U_FULL = [8451.2, 565.5, 1542.4, 795.4, 1156.812339, 0, 1030, 1631.3, 1500]

def _select_active(full_list):
    """Helper to pick only the entries corresponding to CATEGORIES."""
    return [full_list[COS_IDX[name]] for name in CATEGORIES]

COSTS_2020 = _select_active(_COSTS_2020_FULL)
COSTS_2050_0P = _select_active(_COSTS_2050_0P_FULL)
COSTS_2050_15P_F = _select_active(_COSTS_2050_15P_F_FULL)
COSTS_2050_15P_U = _select_active(_COSTS_2050_15P_U_FULL)
COSTS_2050_100P_F = _select_active(_COSTS_2050_100P_F_FULL)
COSTS_2050_100P_U = _select_active(_COSTS_2050_100P_U_FULL)

COLORS = [
    '#2E8B57',  # Labor
    '#4169E1',  # Fixed o&m
    '#FFA500',  # Restart (brighter orange)
    '#696969',  # Depreciation
    '#FF69B4',  # Retirement loss (pink)
    '#9370DB',  # Storage & Capital
    '#FFD700',  # Electricity
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

    # Only show 2050 scenarios; 2020 is the zero baseline and not drawn
    scenarios = ['No overcapacity', '30% overcapacity', 'All overcapacity']
    scenario_costs_2050 = [COSTS_2050_0P, costs_15p, costs_100p]
    # Compute per-category deltas vs 2020 for each 2050 scenario
    deltas_2050 = [[c - b for c, b in zip(costs, COSTS_2020)] for costs in scenario_costs_2050]
    total_deltas_2050 = [sum(d) for d in deltas_2050]

    print(
        f"  [{scenario_type}] Δ 2050_0p: {total_deltas_2050[0]:+.2f}  "
        f"Δ 2050_15p: {total_deltas_2050[1]:+.2f}  Δ 2050_100p: {total_deltas_2050[2]:+.2f} CNY/tonne"
    )

    fig, ax = plt.subplots(
        1,
        1,
        figsize=(_mm_to_inches(FIG_WIDTH_MM), _mm_to_inches(FIG_HEIGHT_MM)),
    )
    x = np.arange(len(scenarios))
    # Make all bars slimmer
    width_main = 0.3
    width_elec = 0.3

    # Stack all non-electricity positive changes upward from zero
    pos_bottom = np.zeros(len(scenarios))
    idx_elec = CATEGORIES.index('Electricity')

    for i, (category, color) in enumerate(zip(CATEGORIES, COLORS)):
        if i == idx_elec:
            continue
        for j in range(len(scenarios)):
            delta = deltas_2050[j][i]
            if delta <= 0:
                continue
            bottom = pos_bottom[j]
            height = delta
            pos_bottom[j] += delta
            label = category if j == 0 else "_nolegend_"
            bar = ax.bar(
                x[j],
                height,
                width_main,
                bottom=bottom,
                label=label,
                color=color,
                alpha=0.8,
            )[0]
            if LABEL_ABS_MIN <= delta < LABEL_ABS_MAX:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bottom + height / 2,
                    f'{delta:+.0f}',
                    ha='center',
                    va='center',
                    fontsize=ANNOTATION_PT,
                    fontweight='bold',
                    color='black',
                )

    # Electricity change: draw separate thin bar, starting from total positive increase
    elec_deltas = [d[idx_elec] for d in deltas_2050]
    neg_min = 0.0
    for j in range(len(scenarios)):
        delta_e = elec_deltas[j]
        if abs(delta_e) < 1e-6:
            continue
        # Slightly to the right of the main bar
        x_e = x[j] + width_main / 2 + width_elec / 2
        if delta_e < 0:
            # Start from the total positive stack and extend downward
            y_top = pos_bottom[j]
            y_bottom = y_top + delta_e  # delta_e is negative
            bottom = y_bottom
            height = y_top - y_bottom
            neg_min = min(neg_min, y_bottom)
        else:
            # If electricity cost increases, start above the positive stack
            bottom = pos_bottom[j]
            height = delta_e
        bar = ax.bar(
            x_e,
            height,
            width_elec,
            bottom=bottom,
            label='Electricity' if j == 0 else "_nolegend_",
            color=COLORS[idx_elec],
            alpha=0.8,
        )[0]
        if LABEL_ABS_MIN <= abs(delta_e) < LABEL_ABS_MAX:
            x_elec = bar.get_x() + bar.get_width() / 2
            y_elec = bottom + height / 2
            label_txt = f'{delta_e:+.0f}'
            kw = dict(fontsize=ANNOTATION_PT, fontweight='bold', color='black', ha='center')
            # 30% overcapacity (j=1): nudge label slightly up — often sits on/near the y=0 line
            if j == 1:
                ax.annotate(
                    label_txt,
                    xy=(x_elec, y_elec),
                    xytext=(0, 7),
                    textcoords='offset points',
                    va='bottom',
                    **kw,
                )
            else:
                ax.text(x_elec, y_elec, label_txt, va='center', **kw)

    ax.axhline(0, color='black', linewidth=1.2)
    ax.set_ylabel('Change in levelized cost (CNY/tonne)', fontsize=TEXT_PT, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(scenarios, fontsize=TEXT_PT)
    # Y-axis: fixed range [-2000, 5000] with 1000-step ticks
    y_min_tick = -2000
    y_max_tick = 5000
    yticks = np.arange(y_min_tick, y_max_tick + 1, 1000)
    ax.set_ylim(y_min_tick, y_max_tick)
    ax.set_yticks(yticks)
    ax.set_yticklabels([f'{int(t):+d}' for t in yticks], fontsize=TEXT_PT)

    # Label total delta for each 2050 scenario with a downward triangle shifted to the right
    for j in range(len(scenarios)):
        delta_tot = total_deltas_2050[j]
        # place marker to the right of electricity bar
        x_tot = x[j] + width_main / 2 + width_elec / 2
        ax.scatter(x_tot, delta_tot, marker='v', color='black', s=24, zorder=5)
        # place text to the right of the marker, vertically centered
        if abs(delta_tot) >= LABEL_ABS_MIN:
            ax.text(
                x_tot + 0.03,
                delta_tot,
                f'{delta_tot:+.0f}',
                ha='left',
                va='center',
                fontsize=ANNOTATION_PT,
                fontweight='bold',
            )

    # Build legend explicitly from all categories to ensure every component appears,
    # even if some have no visible bar in a given scenario.
    from matplotlib.patches import Patch
    legend_handles = [Patch(facecolor=color, label=cat) for cat, color in zip(CATEGORIES, COLORS)]
    ax.legend(
        legend_handles,
        CATEGORIES,
        bbox_to_anchor=(0.5, -0.1),
        loc='upper center',
        ncol=4,
        fontsize=TEXT_PT,
        frameon=False,
    )
    ax.grid(True, alpha=0.3, axis='y')
    plt.tight_layout()

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f'aluminum_cost_change_2020_2050_stacked_bar_s_{scenario_type.upper()}.pdf')
    plt.savefig(output_path, format='pdf', bbox_inches='tight', facecolor='white')
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
