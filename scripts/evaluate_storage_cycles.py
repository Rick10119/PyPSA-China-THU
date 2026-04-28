"""
评估多种储能技术在不同循环周期下的理论套利价值（1 MW 单元）。

方法说明：
1) 从已求解 PyPSA `.nc` 动态读取电价与储能参数；
2) 将全年时序按窗口分块（24/168/730/8760 小时）；
3) 每个分块内用 Top-H / Bottom-H 选择放电/充电小时；
4) electric 技术按电价选放电时段；thermal 技术按替代法等效电价选放热时段；
5) 扣除年化容量成本后得到年度净收益，并绘制多技术对比图。
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd
import pypsa


WINDOWS: dict[str, int] = {
    "Daily (24h)": 24,
    "Weekly (168h)": 168,
    "Monthly (730h)": 730,
    "Annual (8760h)": 8760,
}

H_RULES: dict[str, dict[str, int]] = {
    "electric": {
        "Daily (24h)": 4,
        "Weekly (168h)": 4,
        "Monthly (730h)": 4,
        "Annual (8760h)": 4,
    },
    "thermal": {
        "Daily (24h)": 12,
        "Weekly (168h)": 24,
        "Monthly (730h)": 24,
        "Annual (8760h)": 24,
    },
}


def _annotate_bars(ax, bars, value_fmt: str = "{:,.1f}") -> None:
    """给柱状图添加数值标签。"""
    for bar in bars:
        height = float(bar.get_height())
        if np.isnan(height):
            continue
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height,
            value_fmt.format(height),
            ha="center",
            va="bottom",
            fontsize=8,
        )


def _annotate_points(ax, x_values, y_values, value_fmt: str = "{:,.1f}") -> None:
    """给折线图点添加数值标签。"""
    for x_val, y_val in zip(x_values, y_values):
        y_num = float(y_val)
        if np.isnan(y_num):
            continue
        ax.text(x_val, y_num, value_fmt.format(y_num), ha="center", va="bottom", fontsize=8)


def _mock_cop_series(price_index: pd.Index) -> pd.Series:
    """生成可复现实验用 COP_av 序列（2.0~4.0 之间波动）。"""
    hours = np.arange(len(price_index), dtype=float)
    cop_values = 3.0 + np.sin(2.0 * np.pi * hours / 24.0)
    return pd.Series(cop_values, index=price_index, name="cop_av")


def _match_component(
    index: pd.Index,
    keywords: list[str],
    component_type: str,
    tech_name: str,
    target_bus: str | None = None,
) -> str:
    """
    用关键词自动匹配组件名。
    先尝试“全部关键词命中”，失败后回退到“任一关键词命中”。
    """
    if not keywords:
        raise ValueError(f"技术 `{tech_name}` 的 `{component_type}` 缺少关键词配置。")

    def _match_keyword(name_lower: str, kw_lower: str) -> bool:
        # 纯字母数字关键词采用单词边界匹配，避免 charger 命中 discharger。
        if re.fullmatch(r"[a-z0-9]+", kw_lower):
            return re.search(rf"\b{re.escape(kw_lower)}\b", name_lower) is not None
        return kw_lower in name_lower

    lowered = pd.Index([str(name).lower() for name in index])
    key_lower = [kw.lower() for kw in keywords]

    strict_hits = [
        original
        for original, lower_name in zip(index, lowered)
        if all(_match_keyword(lower_name, kw) for kw in key_lower)
    ]
    if len(strict_hits) > 1 and target_bus:
        bus_token = target_bus.lower()
        scoped_hits = [name for name in strict_hits if bus_token in str(name).lower()]
        if len(scoped_hits) == 1:
            return str(scoped_hits[0])
        if len(scoped_hits) > 1:
            raise ValueError(
                f"技术 `{tech_name}` 的 `{component_type}` 在目标省 `{target_bus}` 内仍匹配到多个候选：{scoped_hits}。"
                f"请收紧关键词：{keywords}"
            )
    if len(strict_hits) == 1:
        return str(strict_hits[0])
    if len(strict_hits) > 1:
        raise ValueError(
            f"技术 `{tech_name}` 的 `{component_type}` 严格匹配到多个候选：{strict_hits}。"
            f"请收紧关键词：{keywords}"
        )

    loose_hits: list[str] = []
    min_hit_required = 1 if len(key_lower) <= 1 else 2
    for original, lower_name in zip(index, lowered):
        hit_count = sum(1 for kw in key_lower if _match_keyword(lower_name, kw))
        if hit_count >= min_hit_required:
            loose_hits.append(str(original))
    if len(loose_hits) > 1 and target_bus:
        bus_token = target_bus.lower()
        scoped_hits = [name for name in loose_hits if bus_token in str(name).lower()]
        if len(scoped_hits) == 1:
            return str(scoped_hits[0])
        if len(scoped_hits) > 1:
            raise ValueError(
                f"技术 `{tech_name}` 的 `{component_type}` 在目标省 `{target_bus}` 内仍匹配到多个候选：{scoped_hits[:10]}。"
                f"请收紧关键词：{keywords}"
            )
    if len(loose_hits) == 1:
        return str(loose_hits[0])
    if len(loose_hits) > 1:
        raise ValueError(
            f"技术 `{tech_name}` 的 `{component_type}` 模糊匹配到多个候选：{loose_hits[:10]}。"
            f"请收紧关键词：{keywords}"
        )

    raise ValueError(
        f"技术 `{tech_name}` 的 `{component_type}` 未匹配到候选。"
        f"关键词：{keywords}。可用组件示例：{list(index[:10])}"
    )


def _resolve_tech_components(
    n: pypsa.Network, tech_cfg: dict[str, Any], target_bus: str
) -> dict[str, str | float]:
    """根据关键词自动定位 store/charge/discharge 组件并提取参数。"""
    tech_name = str(tech_cfg["name"])
    tech_type = str(tech_cfg["type"]).lower()
    if tech_type not in H_RULES:
        raise ValueError(f"技术 `{tech_name}` 的 type=`{tech_type}` 非法，需为 electric 或 thermal。")

    store_name = _match_component(
        n.stores.index, list(tech_cfg["store_keywords"]), "store", tech_name, target_bus
    )
    charge_link = _match_component(
        n.links.index,
        list(tech_cfg["charge_link_keywords"]),
        "charge_link",
        tech_name,
        target_bus,
    )
    discharge_link = _match_component(
        n.links.index,
        list(tech_cfg["discharge_link_keywords"]),
        "discharge_link",
        tech_name,
        target_bus,
    )

    eta_in = float(n.links.loc[charge_link, "efficiency"])
    eta_out = float(n.links.loc[discharge_link, "efficiency"])
    c_annual = float(n.stores.loc[store_name, "capital_cost"])

    if eta_in <= 0 or eta_out <= 0:
        raise ValueError(
            f"技术 `{tech_name}` 的效率必须为正数，当前 eta_in={eta_in}, eta_out={eta_out}"
        )

    return {
        "name": tech_name,
        "type": tech_type,
        "store_name": store_name,
        "charge_link": charge_link,
        "discharge_link": discharge_link,
        "eta_in": eta_in,
        "eta_out": eta_out,
        "c_annual": c_annual,
    }


def evaluate_storage_cycles(
    nc_file_path: str,
    target_bus: str,
    tech_configs: list[dict[str, Any]],
) -> pd.DataFrame:
    """
    评估不同循环周期下多技术 1MW 储能单元的年度净收益。

    参数
    ----------
    nc_file_path : str
        已求解 PyPSA 网络 `.nc` 路径。
    target_bus : str
        目标省份母线名称，例如 "Shandong"。
    tech_configs : list[dict[str, Any]]
        多技术配置列表，示例字段：
        - name
        - type: "electric" 或 "thermal"
        - store_keywords
        - charge_link_keywords
        - discharge_link_keywords
    """
    n = pypsa.Network(nc_file_path)

    # 1) 动态提取电价序列。
    if target_bus not in n.buses_t.marginal_price.columns:
        raise KeyError(f"`{target_bus}` 不在 `n.buses_t.marginal_price` 列中。")
    reconstruct_price = n.buses_t.marginal_price[target_bus].astype(float).copy()
    reconstruct_price.name = "reconstruct_price"
    cop_series = _mock_cop_series(reconstruct_price.index)
    equiv_price_thermal = reconstruct_price / cop_series

    rows: list[dict[str, float | str]] = []
    resolved_techs = [_resolve_tech_components(n, cfg, target_bus) for cfg in tech_configs]

    for tech in resolved_techs:
        tech_name = str(tech["name"])
        tech_type = str(tech["type"])
        eta_in = float(tech["eta_in"])
        eta_out = float(tech["eta_out"])
        c_annual = float(tech["c_annual"])

        for cycle_name, window_size in WINDOWS.items():
            h_hours = int(H_RULES[tech_type][cycle_name])
            if h_hours <= 0:
                raise ValueError(f"技术 `{tech_name}` 在周期 `{cycle_name}` 的 H 非法：{h_hours}")

            price_df = pd.DataFrame(
                {
                    "price": reconstruct_price.values,
                    "equiv_price_thermal": equiv_price_thermal.values,
                }
            )
            price_df["chunk_id"] = np.arange(len(price_df)) // window_size

            grouped_price = price_df.groupby("chunk_id")["price"]

            if tech_type == "thermal":
                grouped_equiv = price_df.groupby("chunk_id")["equiv_price_thermal"]
                bottom_h_sum = grouped_equiv.nsmallest(h_hours).groupby(level=0).sum()
                bottom_h_avg = grouped_equiv.nsmallest(h_hours).groupby(level=0).mean().mean()
                top_h_sum = grouped_equiv.nlargest(h_hours).groupby(level=0).sum()
                top_h_avg = grouped_equiv.nlargest(h_hours).groupby(level=0).mean().mean()
            else:
                bottom_h_sum = grouped_price.nsmallest(h_hours).groupby(level=0).sum()
                bottom_h_avg = grouped_price.nsmallest(h_hours).groupby(level=0).mean().mean()
                top_h_sum = grouped_price.nlargest(h_hours).groupby(level=0).sum()
                top_h_avg = grouped_price.nlargest(h_hours).groupby(level=0).mean().mean()

            discharge_revenue = top_h_sum * eta_out
            charge_cost = bottom_h_sum / eta_in
            gross_chunk = (discharge_revenue - charge_cost).clip(lower=0.0)

            annual_gross = float(gross_chunk.sum())
            annual_cost = float(c_annual * h_hours)
            annual_net = annual_gross - annual_cost
            annual_cycles = int(price_df["chunk_id"].nunique())
            capex_per_cycle = annual_cost / annual_cycles if annual_cycles > 0 else np.nan
            unit_benefit = float(top_h_avg * eta_out)
            unit_cost_energy = float(bottom_h_avg / eta_in)
            unit_cost_capex = float(capex_per_cycle / h_hours)
            unit_cost_total = unit_cost_energy + unit_cost_capex

            rows.append(
                {
                    "tech": tech_name,
                    "tech_type": tech_type,
                    "store_name": str(tech["store_name"]),
                    "charge_link": str(tech["charge_link"]),
                    "discharge_link": str(tech["discharge_link"]),
                    "cycle": cycle_name,
                    "window_hours": float(window_size),
                    "h_hours": float(h_hours),
                    "annual_cycles": float(annual_cycles),
                    "annual_gross": annual_gross,
                    "annual_cost": annual_cost,
                    "annual_net": annual_net,
                    "unit_benefit": unit_benefit,
                    "unit_cost_energy": unit_cost_energy,
                    "capex_per_cycle": float(capex_per_cycle),
                    "unit_cost_capex": unit_cost_capex,
                    "unit_cost_total": unit_cost_total,
                }
            )

    result = pd.DataFrame(rows)

    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        print("警告：未安装 matplotlib，已跳过绘图；如需图形输出请先安装 matplotlib。")
        return result

    # 图1：同一图中比较不同技术、不同循环周期下的年度净收益绝对值。
    cycle_order = list(WINDOWS.keys())
    tech_order = [str(cfg["name"]) for cfg in tech_configs]
    plot_df = (
        result.pivot_table(index="cycle", columns="tech", values="annual_net", aggfunc="first")
        .reindex(index=cycle_order, columns=tech_order)
        .fillna(0.0)
    )

    fig1, ax1 = plt.subplots(figsize=(9, 5))
    x = np.arange(len(cycle_order), dtype=float)
    n_tech = max(len(tech_order), 1)
    total_width = 0.8
    bar_width = total_width / n_tech
    left_start = -total_width / 2 + bar_width / 2
    colors = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2"]

    for idx, tech_name in enumerate(tech_order):
        y_vals = plot_df[tech_name].abs().values
        bars = ax1.bar(
            x + left_start + idx * bar_width,
            y_vals,
            width=bar_width,
            color=colors[idx % len(colors)],
            label=tech_name,
        )
        _annotate_bars(ax1, bars)

    ax1.set_xticks(x)
    ax1.set_xticklabels(cycle_order, rotation=0)
    ax1.set_ylabel("Annual net margin (absolute)")
    ax1.set_title("Annual net margin comparison by cycle and technology")
    ax1.legend(loc="upper left")
    ax1.axhline(0.0, color="black", linewidth=0.8)
    fig1.tight_layout()

    # 图2：统一效益（柱）+ 各技术单位总成本（线）。
    benefit_df = (
        result.pivot_table(index="cycle", columns="tech", values="unit_benefit", aggfunc="first")
        .reindex(index=cycle_order, columns=tech_order)
    )
    cost_df = (
        result.pivot_table(index="cycle", columns="tech", values="unit_cost_total", aggfunc="first")
        .reindex(index=cycle_order, columns=tech_order)
    )
    tech_type_map = {str(tech["name"]): str(tech["type"]).lower() for tech in resolved_techs}
    electric_techs = [name for name in tech_order if tech_type_map.get(name) == "electric"]
    benefit_ref_tech = electric_techs[0] if electric_techs else tech_order[0]
    benefit_vals = benefit_df[benefit_ref_tech].fillna(0.0).values

    fig2, ax2 = plt.subplots(figsize=(9, 5))
    bars_benefit = ax2.bar(
        x,
        benefit_vals,
        width=0.5,
        color="#4C78A8",
        label=f"Unit benefit (reference: {benefit_ref_tech})",
    )
    _annotate_bars(ax2, bars_benefit)

    for idx, tech_name in enumerate(tech_order):
        y_vals = cost_df[tech_name].astype(float).values
        ax2.plot(
            x,
            y_vals,
            marker="o",
            linewidth=2.0,
            color=colors[idx % len(colors)],
            label=f"Unit total cost - {tech_name}",
        )
        _annotate_points(ax2, x, y_vals)

    ax2.set_yscale("log")
    ax2.set_ylabel("Value per 1MWh shifted (log scale)")
    ax2.set_xticks(x)
    ax2.set_xticklabels(cycle_order, rotation=0)
    ax2.set_title("Unit benefit vs. total cost by technology")
    ax2.legend(loc="upper left")
    fig2.tight_layout()

    plt.show()
    return result


if __name__ == "__main__":
    # 你可在此直接修改目标版本路径与组件名。
    nc_file_path = "results/version-0425.1H.1/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2025.nc"
    target_bus = "Shandong"
    tech_configs = [
        {
            "name": "battery",
            "type": "electric",
            "store_keywords": ["battery"],
            "charge_link_keywords": ["battery", "charger"],
            "discharge_link_keywords": ["battery", "discharger"],
        },
        {
            "name": "hydrogen_storage",
            "type": "thermal",
            "store_keywords": ["h2", "store"],
            "charge_link_keywords": ["h2", "electrolysis"],
            "discharge_link_keywords": ["h2", "chp"],
        },
        {
            "name": "hot_water",
            "type": "thermal",
            "store_keywords": ["decentral", "water", "tank"],
            "charge_link_keywords": ["decentral", "water tanks", "charger"],
            "discharge_link_keywords": ["decentral", "water tanks", "discharger"],
        },
    ]

    if not Path(nc_file_path).exists():
        raise FileNotFoundError(
            f"未找到网络文件：{nc_file_path}\n"
            "请确认结果目录已生成，或修改为你的 `.nc` 绝对/相对路径。"
        )

    df_result = evaluate_storage_cycles(
        nc_file_path=nc_file_path,
        target_bus=target_bus,
        tech_configs=tech_configs,
    )
    print(df_result)
