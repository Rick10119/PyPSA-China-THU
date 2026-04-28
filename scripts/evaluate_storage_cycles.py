"""
评估电池储能在不同循环周期下的理论套利价值（1 MW 单元）。

方法说明：
1) 从已求解 PyPSA `.nc` 动态读取电价与储能参数；
2) 将全年时序按窗口分块（24/168/730/8760 小时）；
3) 每个分块内用 Top-H / Bottom-H 选择放电/充电小时；
4) 采用效率修正后计算毛利，若毛利<0 则该块不动作（记 0）；
5) 扣除年化容量成本后得到年度净收益，并绘图对比。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pypsa


def _opt_or_nom(df: pd.DataFrame, index_name: str, opt_col: str, nom_col: str) -> float:
    """优先读取 *_opt，若不存在或为空则回退到名义值。"""
    if index_name not in df.index:
        raise KeyError(f"组件 `{index_name}` 不存在。")
    row = df.loc[index_name]
    if opt_col in row.index and pd.notna(row[opt_col]):
        return float(row[opt_col])
    if nom_col in row.index and pd.notna(row[nom_col]):
        return float(row[nom_col])
    raise ValueError(f"`{index_name}` 缺少 `{opt_col}` 与 `{nom_col}` 的有效数值。")


def _annotate_bars(ax, bars) -> None:
    """给柱状图添加数值标签。"""
    for bar in bars:
        height = float(bar.get_height())
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            height,
            f"{height:,.1f}",
            ha="center",
            va="bottom",
            fontsize=9,
        )


def _annotate_points(ax, x_values, y_values) -> None:
    """给折线图点添加数值标签。"""
    for x_val, y_val in zip(x_values, y_values):
        ax.text(x_val, float(y_val), f"{float(y_val):,.1f}", ha="center", va="bottom", fontsize=9)


def evaluate_storage_cycles(
    nc_file_path: str,
    target_bus: str,
    store_name: str,
    charge_link: str,
    discharge_link: str,
) -> pd.DataFrame:
    """
    评估不同循环周期下 1MW 储能单元的年度净收益。

    参数
    ----------
    nc_file_path : str
        已求解 PyPSA 网络 `.nc` 路径。
    target_bus : str
        目标省份母线名称，例如 "Shandong"。
    store_name : str
        电池 Store 组件名称，例如 "Shandong battery"。
    charge_link : str
        充电 Link 名称，例如 "Shandong battery charger"。
    discharge_link : str
        放电 Link 名称，例如 "Shandong battery discharger"。
    """
    n = pypsa.Network(nc_file_path)

    # 1) 动态提取 8760（或该网络 snapshot 长度）电价序列。
    if target_bus not in n.buses_t.marginal_price.columns:
        raise KeyError(f"`{target_bus}` 不在 `n.buses_t.marginal_price` 列中。")
    reconstruct_price = n.buses_t.marginal_price[target_bus].astype(float).copy()
    reconstruct_price.name = "reconstruct_price"

    # 2) 动态提取效率与年化成本参数。
    if charge_link not in n.links.index:
        raise KeyError(f"充电 Link `{charge_link}` 不存在。")
    if discharge_link not in n.links.index:
        raise KeyError(f"放电 Link `{discharge_link}` 不存在。")
    if store_name not in n.stores.index:
        raise KeyError(f"Store `{store_name}` 不存在。")

    eta_in = float(n.links.loc[charge_link, "efficiency"])
    eta_out = float(n.links.loc[discharge_link, "efficiency"])
    c_annual = float(n.stores.loc[store_name, "capital_cost"])

    if eta_in <= 0 or eta_out <= 0:
        raise ValueError(f"效率必须为正数，当前 eta_in={eta_in}, eta_out={eta_out}")

    # 3) 动态计算 H = round(E_cap / P_cap)。
    e_cap = _opt_or_nom(n.stores, store_name, "e_nom_opt", "e_nom")
    p_cap = _opt_or_nom(n.links, charge_link, "p_nom_opt", "p_nom")
    if p_cap <= 0:
        raise ValueError(f"充电功率上限 P_cap 必须大于 0，当前 P_cap={p_cap}")
    h_hours = int(round(e_cap / p_cap))
    if h_hours <= 0:
        raise ValueError(
            f"H=round(E_cap/P_cap) 结果为 {h_hours}，无法执行套利评估。"
            "请检查容量参数或改用默认值。"
        )

    # 循环窗口定义：日 / 周 / 月 / 年。
    windows = {
        "Daily (24h)": 24,
        "Weekly (168h)": 168,
        "Monthly (730h)": 730,
        "Annual (8760h)": 8760,
    }

    rows: list[dict[str, float | str]] = []
    # 只对 4 个窗口迭代；分块内的选时计算完全依赖 pandas groupby + nlargest/nsmallest。
    for cycle_name, window_size in windows.items():
        price_df = pd.DataFrame({"price": reconstruct_price.values})
        price_df["chunk_id"] = np.arange(len(price_df)) // window_size
        grouped = price_df.groupby("chunk_id")["price"]

        # 分块选取 Top-H 与 Bottom-H 小时，进行收益与成本计算。
        top_h_sum = grouped.nlargest(h_hours).groupby(level=0).sum()
        bottom_h_sum = grouped.nsmallest(h_hours).groupby(level=0).sum()

        discharge_revenue = top_h_sum * eta_out
        charge_cost = bottom_h_sum / eta_in
        gross_chunk = (discharge_revenue - charge_cost).clip(lower=0.0)

        annual_gross = float(gross_chunk.sum())
        annual_cost = float(c_annual * h_hours)
        annual_net = annual_gross - annual_cost
        annual_cycles = int(price_df["chunk_id"].nunique())
        capex_per_cycle = annual_cost / annual_cycles if annual_cycles > 0 else np.nan

        # 单位搬运 1MWh 的收益/成本：先在每个块内求均价，再跨块平均。
        top_h_avg = grouped.nlargest(h_hours).groupby(level=0).mean().mean()
        bottom_h_avg = grouped.nsmallest(h_hours).groupby(level=0).mean().mean()
        unit_benefit = float(top_h_avg * eta_out)
        unit_cost_energy = float(bottom_h_avg / eta_in)
        # 把年化投资成本摊到单次循环，再折算到每搬运 1MWh。
        unit_cost_capex = float(capex_per_cycle / h_hours)
        unit_cost_total = unit_cost_energy + unit_cost_capex

        rows.append(
            {
                "cycle": cycle_name,
                "window_hours": float(window_size),
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

    result = pd.DataFrame(rows).set_index("cycle")
    result.index.name = "cycle"

    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError:
        print("警告：未安装 matplotlib，已跳过绘图；如需图形输出请先安装 matplotlib。")
        return result

    # 图1：不同循环周期下的年度净收益。
    fig1, ax1 = plt.subplots(figsize=(9, 5))
    x = np.arange(len(result))
    bars_net = ax1.bar(x, result["annual_net"].values, color="#4C78A8")
    ax1.set_xticks(x)
    ax1.set_xticklabels(result.index, rotation=0)
    ax1.set_ylabel("Annual net margin")
    ax1.set_title("Annual net margin of 1MW storage by cycle window")
    _annotate_bars(ax1, bars_net)
    ax1.axhline(0.0, color="black", linewidth=0.8)
    fig1.tight_layout()

    # 图2：不同循环周期下，搬运 1MWh 的收益（柱）与总成本（曲线）对比。
    # 使用单一纵轴，并设为对数坐标，便于跨数量级比较。
    fig2, ax2 = plt.subplots(figsize=(9, 5))
    bars_benefit = ax2.bar(x, result["unit_benefit"].values, width=0.5, label="Unit benefit (discharge)")
    cost_line = ax2.plot(
        x,
        result["unit_cost_total"].values,
        color="#F58518",
        marker="o",
        linewidth=2.0,
        label="Unit total cost (energy + amortized capex)",
    )
    ax2.set_yscale("log")
    ax2.set_ylabel("Value per 1MWh shifted (log scale)")
    ax2.set_xticks(x)
    ax2.set_xticklabels(result.index, rotation=0)
    ax2.set_title("Unit benefit vs. total cost per 1MWh shifted")

    handles, labels = ax2.get_legend_handles_labels()
    ax2.legend(handles, labels, loc="upper left")
    _annotate_bars(ax2, bars_benefit)
    _annotate_points(ax2, x, result["unit_cost_total"].values)
    fig2.tight_layout()

    plt.show()
    return result


if __name__ == "__main__":
    # 你可在此直接修改目标版本路径与组件名。
    nc_file_path = "results/version-0425.1H.1/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2025.nc"
    target_bus = "Shandong"
    store_name = "Shandong battery"
    charge_link = "Shandong battery charger"
    discharge_link = "Shandong battery discharger"

    if not Path(nc_file_path).exists():
        raise FileNotFoundError(
            f"未找到网络文件：{nc_file_path}\n"
            "请确认结果目录已生成，或修改为你的 `.nc` 绝对/相对路径。"
        )

    df_result = evaluate_storage_cycles(
        nc_file_path=nc_file_path,
        target_bus=target_bus,
        store_name=store_name,
        charge_link=charge_link,
        discharge_link=discharge_link,
    )
    print(df_result)
