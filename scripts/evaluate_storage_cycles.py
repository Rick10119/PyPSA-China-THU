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

H_RULES_BY_TECH: dict[str, dict[str, float]] = {
    "battery": {
        "Daily (24h)": 2.6,
        "Weekly (168h)": 6,
        "Monthly (730h)": 168,
        "Annual (8760h)": 168,
    },
    "hydrogen_storage": {
        "Daily (24h)": 6,
        "Weekly (168h)": 24,
        "Monthly (730h)": 168,
        "Annual (8760h)": 168,
    },
    # 分布式热水按用户要求：月及以上使用 73 小时。
    "hot_water": {
        "Daily (24h)": 2.6,
        "Weekly (168h)": 24,
        "Monthly (730h)": 73,
        "Annual (8760h)": 73,
    },
}

H_RULES_BY_TYPE_FALLBACK: dict[str, dict[str, float]] = {
    "electric": {
        "Daily (24h)": 2.6,
        "Weekly (168h)": 6,
        "Monthly (730h)": 168,
        "Annual (8760h)": 168,
    },
    "thermal": {
        "Daily (24h)": 2.6,
        "Weekly (168h)": 24,
        "Monthly (730h)": 168,
        "Annual (8760h)": 168,
    },
}

# value 统一口径：仅按周期取原始电价 top-H 均价，不区分技术。
VALUE_H_BY_CYCLE: dict[str, float] = {
    "Daily (24h)": 2.6,
    "Weekly (168h)": 24,
    "Monthly (730h)": 168,
    "Annual (8760h)": 168,
}


def _calculate_annuity(lifetime: float, discount_rate: float) -> float:
    """与项目 add_electricity.py 一致的年化系数。"""
    if discount_rate == 0:
        return 1 / lifetime
    return discount_rate / (1.0 - 1.0 / (1.0 + discount_rate) ** lifetime)


def _infer_costs_file_from_nc(nc_file_path: str) -> str:
    """从 nc 路径推断 costs_YYYY.csv，找不到直接报错。"""
    years = re.findall(r"(20\d{2})", nc_file_path)
    if years:
        candidate = Path("data/costs") / f"costs_{years[-1]}.csv"
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(
        f"未找到对应年份成本文件：{Path('data/costs') / f'costs_{years[-1]}.csv' if years else 'data/costs/costs_YYYY.csv'}"
    )


def _build_cost_lookup(costs_csv_path: str) -> dict[tuple[str, str], float]:
    """读取 costs_YYYY.csv 并构建 (technology, parameter)->value 查询。"""
    cost_df = pd.read_csv(costs_csv_path)
    lookup: dict[tuple[str, str], float] = {}
    for _, row in cost_df.iterrows():
        tech = str(row["technology"]).strip()
        param = str(row["parameter"]).strip()
        lookup[(tech, param)] = float(row["value"])
    return lookup


def _annualized_capex_from_cost_file(
    cost_lookup: dict[tuple[str, str], float],
    technology_name: str,
    *,
    default_discount_rate: float = 0.04,
) -> float:
    """从成本文件计算年化 capital_cost（仍为 investment 同口径单位的每年值）。"""
    inv = cost_lookup[(technology_name, "investment")]
    lifetime = cost_lookup[(technology_name, "lifetime")]
    fom = cost_lookup.get((technology_name, "FOM"), 0.0)
    discount = cost_lookup.get((technology_name, "discount rate"), default_discount_rate)
    return (_calculate_annuity(lifetime, discount) + fom / 100.0) * inv


def _convert_to_per_mw_year(value: float, unit_type: str) -> float:
    """
    将成本换算为每 1MW 功率侧年成本（EUR/MW-year）或每 1MWh 能量侧年成本（EUR/MWh-year）。
    - power_kw: 输入为 EUR/kW-year，乘 1000。
    - energy_kwh: 输入为 EUR/kWh-year，乘 1000。
    - passthrough: 已是 MW/MWh 口径，不变。
    """
    if unit_type in {"power_kw", "energy_kwh"}:
        return value * 1000.0
    return value


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


def _resolve_cop_series_from_links(
    n: pypsa.Network,
    snapshot_index: pd.Index,
    cop_link_names: list[str],
) -> pd.Series:
    """从 nc 中读取热泵时变效率并合成为时变 COP 序列。"""
    if not cop_link_names:
        raise ValueError("未提供 COP 参考链路。")

    missing = [name for name in cop_link_names if name not in n.links_t.efficiency.columns]
    if missing:
        raise ValueError(f"COP 参考链路缺少时变效率列：{missing}")

    eff_df = n.links_t.efficiency[cop_link_names].reindex(snapshot_index).astype(float)
    if hasattr(n.links_t, "p0") and all(name in n.links_t.p0.columns for name in cop_link_names):
        weight_df = n.links_t.p0[cop_link_names].reindex(snapshot_index).abs().astype(float)
        denom = weight_df.sum(axis=1)
        weighted = (eff_df * weight_df).sum(axis=1)
        cop = weighted.where(denom > 1e-9, np.nan) / denom.where(denom > 1e-9, np.nan)
        fallback = eff_df.mean(axis=1)
        cop = cop.fillna(fallback)
    else:
        cop = eff_df.mean(axis=1)
    return cop.clip(lower=1e-6).rename("cop_av_from_nc")


def _resolve_heating_season_mask(
    snapshot_index: pd.Index,
    start_month: int = 11,
    start_day: int = 1,
    end_month: int = 3,
    end_day: int = 15,
) -> pd.Series:
    """
    供热季门槛：默认 11 月 1 日到次年 3 月 15 日（含边界）。
    跨年窗口采用 OR 逻辑拼接。
    """
    ts = pd.to_datetime(snapshot_index)
    md = ts.month * 100 + ts.day
    start_md = start_month * 100 + start_day
    end_md = end_month * 100 + end_day
    if start_md <= end_md:
        mask = (md >= start_md) & (md <= end_md)
    else:
        mask = (md >= start_md) | (md <= end_md)
    return pd.Series(mask, index=snapshot_index, name="heating_season_mask")


def _weighted_extreme_stats(
    values: pd.Series,
    group_ids: pd.Series,
    h_hours: float,
    *,
    largest: bool,
) -> tuple[pd.Series, pd.Series]:
    """
    计算每个分组的加权 top/bottom-H 总和与均值，支持非整数 H。
    例如 H=2.6 时：前 2 个小时全额 + 第 3 个小时 0.6 权重。
    """
    if h_hours <= 0:
        raise ValueError(f"h_hours 必须大于 0，当前={h_hours}")
    h_floor = int(np.floor(h_hours))
    h_frac = float(h_hours - h_floor)
    required = h_floor + (1 if h_frac > 1e-12 else 0)

    sums: dict[int, float] = {}
    means: dict[int, float] = {}
    for gid, idx in group_ids.groupby(group_ids).groups.items():
        arr = values.loc[idx].dropna().astype(float).sort_values(ascending=not largest).values
        if len(arr) < required:
            continue
        total = float(arr[:h_floor].sum()) if h_floor > 0 else 0.0
        if h_frac > 1e-12:
            total += float(arr[h_floor]) * h_frac
        sums[int(gid)] = total
        means[int(gid)] = total / h_hours
    return pd.Series(sums, dtype=float), pd.Series(means, dtype=float)


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
        # 由字母数字+空格组成的短语，也采用词边界匹配，避免 central 命中 decentral。
        if re.fullmatch(r"[a-z0-9 ]+", kw_lower):
            phrase = re.sub(r"\s+", r"\\s+", kw_lower.strip())
            return re.search(rf"\b{phrase}\b", name_lower) is not None
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
    n: pypsa.Network,
    tech_cfg: dict[str, Any],
    target_bus: str,
    cost_lookup: dict[tuple[str, str], float] | None = None,
) -> dict[str, str | float] | None:
    """优先按成本文件模式；否则按网络组件匹配并提取参数。"""
    tech_name = str(tech_cfg["name"])
    tech_type = str(tech_cfg["type"]).lower()
    if tech_type not in H_RULES_BY_TYPE_FALLBACK:
        raise ValueError(f"技术 `{tech_name}` 的 type=`{tech_type}` 非法，需为 electric 或 thermal。")

    cop_links: list[str] = []
    cop_link_groups = tech_cfg.get("cop_link_groups", [])
    if cop_link_groups:
        for idx, kw_group in enumerate(cop_link_groups):
            cop_links.append(
                _match_component(
                    n.links.index,
                    list(kw_group),
                    f"cop_link[{idx}]",
                    tech_name,
                    target_bus,
                )
            )

    # 成本文件驱动模式：用于网络中缺少特定组件（如 H2 fuel cell）时，仍可评估。
    cost_file_components = tech_cfg.get("cost_file_components")
    if cost_file_components is not None:
        if cost_lookup is None:
            raise ValueError(f"技术 `{tech_name}` 启用了成本文件模式，但未提供 cost_lookup。")

        store_tech = str(cost_file_components["store_tech"])
        charge_tech = str(cost_file_components["charge_tech"])
        discharge_tech = str(cost_file_components["discharge_tech"])
        include_power_capex = bool(cost_file_components.get("include_power_capex", True))
        charge_cost_multiplier = float(cost_file_components.get("charge_cost_multiplier", 1.0))
        discharge_cost_multiplier = float(cost_file_components.get("discharge_cost_multiplier", 1.0))
        use_sqrt_efficiency = bool(cost_file_components.get("use_sqrt_efficiency", False))

        store_cost_annual_raw = _annualized_capex_from_cost_file(cost_lookup, store_tech)
        c_store_annual = _convert_to_per_mw_year(store_cost_annual_raw, "energy_kwh")
        if include_power_capex:
            charge_cost_annual_raw = _annualized_capex_from_cost_file(cost_lookup, charge_tech)
            discharge_cost_annual_raw = _annualized_capex_from_cost_file(cost_lookup, discharge_tech)
            c_power_annual = (
                _convert_to_per_mw_year(charge_cost_annual_raw, "power_kw") * charge_cost_multiplier
                + _convert_to_per_mw_year(discharge_cost_annual_raw, "power_kw") * discharge_cost_multiplier
            )
        else:
            c_power_annual = 0.0

        eta_in_raw = float(cost_lookup[(charge_tech, "efficiency")])
        eta_out_raw = float(cost_lookup[(discharge_tech, "efficiency")])
        if use_sqrt_efficiency:
            # 例如 battery inverter 仅给 round-trip 效率时，按对称充放电取平方根。
            eta_in = float(np.sqrt(eta_in_raw))
            eta_out = float(np.sqrt(eta_out_raw))
        else:
            eta_in = eta_in_raw
            eta_out = eta_out_raw

        if eta_in <= 0 or eta_out <= 0:
            raise ValueError(
                f"技术 `{tech_name}`（成本文件模式）效率必须为正数，当前 eta_in={eta_in}, eta_out={eta_out}"
            )

        discharge_gate_keywords = tech_cfg.get("discharge_value_requires_link_keywords")
        discharge_gate_link = ""
        if discharge_gate_keywords:
            discharge_gate_link = _match_component(
                n.links.index,
                list(discharge_gate_keywords),
                "discharge_gate_link",
                tech_name,
                target_bus,
            )

        thermal_output_factor = float(tech_cfg.get("thermal_output_factor", eta_out))
        thermal_output_link_keywords = tech_cfg.get("thermal_output_link_keywords")
        if thermal_output_link_keywords:
            thermal_output_link = _match_component(
                n.links.index,
                list(thermal_output_link_keywords),
                "thermal_output_link",
                tech_name,
                target_bus,
            )
            if "efficiency2" in n.links.columns and pd.notna(n.links.loc[thermal_output_link, "efficiency2"]):
                thermal_output_factor = float(n.links.loc[thermal_output_link, "efficiency2"])
            elif "efficiency" in n.links.columns and pd.notna(n.links.loc[thermal_output_link, "efficiency"]):
                thermal_output_factor = float(n.links.loc[thermal_output_link, "efficiency"])

        return {
            "name": tech_name,
            "type": tech_type,
            "store_name": f"cost_file::{store_tech}",
            "charge_link": f"cost_file::{charge_tech}",
            "discharge_link": f"cost_file::{discharge_tech}",
            "power_cost_links": f"{charge_tech}|{discharge_tech}",
            "eta_in": eta_in,
            "eta_out": eta_out,
            "c_store_annual": c_store_annual,
            "c_power_annual": c_power_annual,
            "discharge_gate_link": discharge_gate_link,
            "cop_links": "|".join(cop_links),
            "thermal_output_factor": thermal_output_factor,
        }

    allow_missing = bool(tech_cfg.get("allow_missing", False))
    try:
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
    except ValueError as exc:
        if allow_missing:
            print(f"提示：跳过技术 `{tech_name}`，原因：{exc}")
            return None
        raise

    eta_in = float(n.links.loc[charge_link, "efficiency"])
    eta_out = float(n.links.loc[discharge_link, "efficiency"])
    c_store_annual = float(n.stores.loc[store_name, "capital_cost"])

    power_cost_groups = tech_cfg.get("power_cost_link_groups")
    if power_cost_groups is None:
        power_cost_groups = [
            list(tech_cfg["charge_link_keywords"]),
            list(tech_cfg["discharge_link_keywords"]),
        ]

    power_cost_links: list[str] = []
    for idx, kw_group in enumerate(power_cost_groups):
        try:
            matched_link = _match_component(
                n.links.index,
                list(kw_group),
                f"power_cost_link[{idx}]",
                tech_name,
                target_bus,
            )
        except ValueError as exc:
            if allow_missing:
                print(f"提示：技术 `{tech_name}` 未找到某个功率成本组件，已忽略：{exc}")
                continue
            raise
        if matched_link not in power_cost_links:
            power_cost_links.append(matched_link)
    c_power_annual = float(n.links.loc[power_cost_links, "capital_cost"].sum()) if power_cost_links else 0.0

    discharge_gate_keywords = tech_cfg.get("discharge_value_requires_link_keywords")
    discharge_gate_link = None
    if discharge_gate_keywords:
        try:
            discharge_gate_link = _match_component(
                n.links.index,
                list(discharge_gate_keywords),
                "discharge_gate_link",
                tech_name,
                target_bus,
            )
        except ValueError as exc:
            if allow_missing:
                print(f"提示：技术 `{tech_name}` 未找到放热门槛参考链路，已忽略：{exc}")
            else:
                raise

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
        "power_cost_links": "|".join(power_cost_links),
        "eta_in": eta_in,
        "eta_out": eta_out,
        "c_store_annual": c_store_annual,
        "c_power_annual": c_power_annual,
        "discharge_gate_link": discharge_gate_link if discharge_gate_link is not None else "",
        "cop_links": "|".join(cop_links),
        "thermal_output_factor": eta_out,
    }


def _get_h_hours(tech_name: str, tech_type: str, cycle_name: str) -> float:
    """按技术优先、按类型兜底获取 H。"""
    if tech_name in H_RULES_BY_TECH and cycle_name in H_RULES_BY_TECH[tech_name]:
        return float(H_RULES_BY_TECH[tech_name][cycle_name])
    if tech_type in H_RULES_BY_TYPE_FALLBACK and cycle_name in H_RULES_BY_TYPE_FALLBACK[tech_type]:
        return float(H_RULES_BY_TYPE_FALLBACK[tech_type][cycle_name])
    raise KeyError(f"未找到技术 `{tech_name}`（type={tech_type}）在周期 `{cycle_name}` 的 H 配置。")


def evaluate_storage_cycles(
    nc_file_path: str,
    target_bus: str,
    tech_configs: list[dict[str, Any]],
    segmented_price_file: str,
    costs_csv_path: str | None = None,
    plots_output_dir: str | None = None,
    cny_per_eur: float = 7.8,
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
    if costs_csv_path is None:
        costs_csv_path = _infer_costs_file_from_nc(nc_file_path)
    cost_lookup = _build_cost_lookup(costs_csv_path)
    if plots_output_dir is None:
        nc_path = Path(nc_file_path).resolve()
        plots_output_dir = str(nc_path.parent.parent / "plots")
    plots_dir = Path(plots_output_dir)
    plots_dir.mkdir(parents=True, exist_ok=True)

    # 1) 电价序列：必须来自 segmented price 文件；不存在或不匹配直接报错。
    price_path = Path(segmented_price_file)
    if not price_path.is_absolute():
        price_path = Path.cwd() / price_path
    if not price_path.exists():
        raise FileNotFoundError(f"未找到 segmented price 文件：{price_path}")

    if price_path.suffix.lower() in {".parquet", ".pq"}:
        price_df = pd.read_parquet(price_path)
    else:
        price_df = pd.read_csv(price_path)
    price_df.columns = [str(c).strip() for c in price_df.columns]
    target_bus_norm = target_bus.strip()
    snapshots_norm = pd.to_datetime(n.snapshots)

    reconstruct_price: pd.Series | None = None
    if "snapshot" in price_df.columns:
        price_df["snapshot"] = pd.to_datetime(price_df["snapshot"])
        price_df = price_df.set_index("snapshot", drop=True)
        price_df = price_df[~price_df.index.duplicated(keep="first")]

    # 常见格式 1：wide（列名含目标省）
    if target_bus_norm in price_df.columns:
        if isinstance(price_df.index, pd.DatetimeIndex):
            s = price_df[target_bus_norm].astype(float)
            s = s.reindex(snapshots_norm)
            if s.isna().any():
                s = s.interpolate(limit_direction="both")
            reconstruct_price = pd.Series(s.values, index=n.snapshots, name="reconstruct_price")
        else:
            reconstruct_price = pd.Series(
                price_df[target_bus_norm].astype(float).values,
                index=n.snapshots[: len(price_df)],
                name="reconstruct_price",
            )
    else:
        # 宽表大小写/空格宽松匹配
        wide_match = [c for c in price_df.columns if c.strip().lower() == target_bus_norm.lower()]
        if wide_match:
            col = wide_match[0]
            if isinstance(price_df.index, pd.DatetimeIndex):
                s = price_df[col].astype(float)
                s = s.reindex(snapshots_norm)
                if s.isna().any():
                    s = s.interpolate(limit_direction="both")
                reconstruct_price = pd.Series(s.values, index=n.snapshots, name="reconstruct_price")
            else:
                reconstruct_price = pd.Series(
                    price_df[col].astype(float).values,
                    index=n.snapshots[: len(price_df)],
                    name="reconstruct_price",
                )
        # 常见格式 2：long（province/bus + price）
        # 常见格式 2：long（province/bus + price）
        if reconstruct_price is None:
            col_lower_map = {c.lower(): c for c in price_df.columns}
            bus_col = None
            for key in ("province", "bus", "node", "region"):
                if key in col_lower_map:
                    bus_col = col_lower_map[key]
                    break
            price_col = None
            for key in ("price", "reconstruct_price", "segmented_price", "marginal_price"):
                if key in col_lower_map:
                    price_col = col_lower_map[key]
                    break
            if bus_col and price_col:
                sub = price_df[price_df[bus_col].astype(str).str.strip().str.lower() == target_bus_norm.lower()]
                if len(sub) > 0:
                    if "snapshot" in sub.columns:
                        sub = sub.set_index(pd.to_datetime(sub["snapshot"]))
                        s = sub[price_col].astype(float).reindex(snapshots_norm)
                        if s.isna().any():
                            s = s.interpolate(limit_direction="both")
                        reconstruct_price = pd.Series(s.values, index=n.snapshots, name="reconstruct_price")
                    else:
                        reconstruct_price = pd.Series(
                            sub[price_col].astype(float).values,
                            index=n.snapshots[: len(sub)],
                            name="reconstruct_price",
                        )

    if reconstruct_price is None:
        raise KeyError(
            f"segmented price 文件中找不到目标节点 `{target_bus}` 的价格列或记录：{price_path}"
        )
    if cny_per_eur <= 0:
        raise ValueError(f"`cny_per_eur` 必须为正数，当前={cny_per_eur}")

    rows: list[dict[str, float | str]] = []
    resolved_techs = []
    for cfg in tech_configs:
        resolved = _resolve_tech_components(n, cfg, target_bus, cost_lookup=cost_lookup)
        if resolved is not None:
            resolved_techs.append(resolved)
    if not resolved_techs:
        raise ValueError("没有可用技术可评估：请检查 tech_configs 与网络组件命名。")

    # 周期统一 value（不区分技术）：chunk 内 top-H 原始电价均值，再跨 chunk 平均。
    cycle_value_reference: dict[str, float] = {}
    base_price_df = pd.DataFrame({"price": reconstruct_price.values})
    for cycle_name, window_size in WINDOWS.items():
        h_value = float(VALUE_H_BY_CYCLE.get(cycle_name, 1.0))
        if h_value <= 0:
            h_value = 1.0
        base_price_df["chunk_id"] = np.arange(len(base_price_df)) // window_size
        top_h_sum_ref, top_h_mean_ref = _weighted_extreme_stats(
            base_price_df["price"],
            base_price_df["chunk_id"],
            h_value,
            largest=True,
        )
        top_h_avg_ref = top_h_mean_ref.mean()
        cycle_value_reference[cycle_name] = float(top_h_avg_ref)

    for tech in resolved_techs:
        tech_name = str(tech["name"])
        tech_type = str(tech["type"])
        eta_in = float(tech["eta_in"])
        eta_out = float(tech["eta_out"])
        c_store_annual = float(tech["c_store_annual"])
        c_power_annual = float(tech["c_power_annual"])
        # 成本文件为 EUR 口径；价格为 CNY 口径时，需要先统一货币单位。
        c_store_annual *= cny_per_eur
        c_power_annual *= cny_per_eur
        discharge_gate_link = str(tech["discharge_gate_link"])
        thermal_output_factor = float(tech["thermal_output_factor"])
        cop_links_raw = str(tech.get("cop_links", ""))
        if tech_type == "thermal":
            cop_link_names = [x for x in cop_links_raw.split("|") if x]
            if not cop_link_names:
                raise ValueError(f"技术 `{tech_name}` 为 thermal，但未配置 `cop_link_groups`。")
            cop_series = _resolve_cop_series_from_links(n, reconstruct_price.index, cop_link_names)
            equiv_price_thermal = reconstruct_price / cop_series
        else:
            equiv_price_thermal = reconstruct_price
        discharge_gate_mask = None
        if discharge_gate_link:
            dispatch = None
            if discharge_gate_link in n.links_t.p0.columns:
                dispatch = n.links_t.p0[discharge_gate_link].reindex(reconstruct_price.index).fillna(0.0)
            elif discharge_gate_link in n.links_t.p1.columns:
                dispatch = n.links_t.p1[discharge_gate_link].reindex(reconstruct_price.index).fillna(0.0)
            if dispatch is not None:
                discharge_gate_mask = dispatch.abs() > 1e-6
        demand_gate_mask = None
        if tech_type == "thermal":
            # 用户口径：直接按固定供热季判定供热需求门槛。
            demand_gate_mask = _resolve_heating_season_mask(reconstruct_price.index)

        for cycle_name, window_size in WINDOWS.items():
            h_hours = _get_h_hours(tech_name, tech_type, cycle_name)
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
                if discharge_gate_mask is not None or demand_gate_mask is not None:
                    valid_mask = pd.Series(True, index=price_df.index)
                    if discharge_gate_mask is not None:
                        valid_mask = valid_mask & discharge_gate_mask.values
                    if demand_gate_mask is not None:
                        valid_mask = valid_mask & demand_gate_mask.values
                else:
                    valid_mask = pd.Series(True, index=price_df.index)

                # 仅使用“可放热”小时参与放热选时；若某块有效小时 < H，则该块视作不循环。
                discharge_value_series = price_df["equiv_price_thermal"].where(valid_mask, np.nan)
                valid_counts = valid_mask.groupby(price_df["chunk_id"]).sum()
                active_chunks = valid_counts[valid_counts >= int(np.ceil(h_hours))].index
                active_chunk_mask = price_df["chunk_id"].isin(active_chunks)
                top_h_sum_raw, top_h_mean_raw = _weighted_extreme_stats(
                    discharge_value_series,
                    price_df["chunk_id"],
                    h_hours,
                    largest=True,
                )
                top_h_sum = top_h_sum_raw.reindex(active_chunks).fillna(0.0)
                top_h_avg = top_h_mean_raw.reindex(active_chunks).mean()

                # 只有可执行块才进行充热成本计算；不可执行块视作“不动作”（少一次循环）。
                charge_value_series = price_df["equiv_price_thermal"].where(active_chunk_mask, np.nan)
                bottom_h_sum_raw, bottom_h_mean_raw = _weighted_extreme_stats(
                    charge_value_series,
                    price_df["chunk_id"],
                    h_hours,
                    largest=False,
                )
                bottom_h_sum = bottom_h_sum_raw.reindex(active_chunks).fillna(0.0)
                bottom_h_avg = bottom_h_mean_raw.reindex(active_chunks).mean()
            else:
                top_h_sum, top_h_mean = _weighted_extreme_stats(
                    price_df["price"],
                    price_df["chunk_id"],
                    h_hours,
                    largest=True,
                )
                bottom_h_sum, bottom_h_mean = _weighted_extreme_stats(
                    price_df["price"],
                    price_df["chunk_id"],
                    h_hours,
                    largest=False,
                )
                top_h_avg = top_h_mean.mean()
                bottom_h_avg = bottom_h_mean.mean()

            if tech_type == "thermal":
                discharge_revenue = top_h_sum * thermal_output_factor
            else:
                discharge_revenue = top_h_sum * eta_out
            charge_cost = bottom_h_sum / eta_in
            gross_chunk = (discharge_revenue - charge_cost).clip(lower=0.0)

            annual_gross = float(gross_chunk.sum())
            annual_cost_store = float(c_store_annual * h_hours)
            annual_cost_power = float(c_power_annual)
            annual_cost = annual_cost_store + annual_cost_power
            annual_net = annual_gross - annual_cost
            if tech_type == "thermal":
                annual_cycles = int(len(active_chunks))
            else:
                annual_cycles = int(price_df["chunk_id"].nunique())
            capex_per_cycle = annual_cost / annual_cycles if annual_cycles > 0 else np.nan
            unit_benefit = float(cycle_value_reference[cycle_name])
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
                    "power_cost_links": str(tech["power_cost_links"]),
                    "cycle": cycle_name,
                    "window_hours": float(window_size),
                    "h_hours": float(h_hours),
                    "annual_cycles": float(annual_cycles),
                    "annual_gross": annual_gross,
                    "annual_cost_store": annual_cost_store,
                    "annual_cost_power": annual_cost_power,
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

    # 仅保留：value 与 cost 随 cycle_name 的折线图（日→周→月→年）。
    cycle_order = list(WINDOWS.keys())
    tech_order = list(dict.fromkeys(result["tech"].tolist()))
    value_df = (
        result.pivot_table(index="cycle", columns="tech", values="unit_benefit", aggfunc="first")
        .reindex(index=cycle_order, columns=tech_order)
    )
    cost_df = (
        result.pivot_table(index="cycle", columns="tech", values="unit_cost_total", aggfunc="first")
        .reindex(index=cycle_order, columns=tech_order)
    )

    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.array([float(WINDOWS[c]) for c in cycle_order], dtype=float)
    colors = ["#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2"]
    # value 已统一为技术无关，图中仅展示一条公共 value 曲线。
    value_shared_raw = value_df.iloc[:, 0].astype(float).values if not value_df.empty else np.array([])
    value_shared = np.where(value_shared_raw > 0.0, value_shared_raw, np.nan)
    ax.plot(
        x,
        value_shared,
        marker="o",
        linewidth=2.4,
        color="#2C7FB8",
        linestyle="-",
        label="electricity shift value",
    )
    for idx, tech_name in enumerate(tech_order):
        color = colors[idx % len(colors)]
        cost_vals_raw = cost_df[tech_name].astype(float).values
        # 对数坐标下跳过非正值点，避免绘图报错。
        cost_vals = np.where(cost_vals_raw > 0.0, cost_vals_raw, np.nan)
        ax.plot(
            x,
            cost_vals,
            marker="s",
            linewidth=2.0,
            color=color,
            linestyle="--",
            label=f"{tech_name} shift cost",
        )

    ax.set_xscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(cycle_order, rotation=0)
    ax.set_yscale("log")
    ax.set_ylabel("Value / Cost per 1MWh shifted")
    ax.set_title("Value and Cost vs Cycle Window")
    ax.legend(loc="best", ncol=2)
    fig.tight_layout()
    fig.savefig(plots_dir / f"{target_bus.lower()}_value_cost_vs_cycle.png", dpi=300, bbox_inches="tight")
    plt.close(fig)

    return result


if __name__ == "__main__":
    # 你可在此直接修改目标版本路径与组件名。
    nc_file_path = "results/version-0425.1H.1/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2025.nc"
    costs_csv_path = _infer_costs_file_from_nc(nc_file_path)
    target_bus = "Shandong"
    segmented_price_file = (
        "results/version-0425.1H.1/prices/reconstructed_from_nc/positive/"
        "reconstructed_prices-ll-current+FCG-linear2050-2025.csv"
    )
    tech_configs = [
        {
            "name": "battery",
            "type": "electric",
            "cost_file_components": {
                "store_tech": "battery storage",
                "charge_tech": "battery inverter",
                "discharge_tech": "battery inverter",
                # 网络中 charger/discharger 各承担 0.5 个 inverter 年化成本。
                "charge_cost_multiplier": 0.5,
                "discharge_cost_multiplier": 0.5,
                "use_sqrt_efficiency": True,
                "include_power_capex": True,
            },
        },
        {
            "name": "hydrogen_fuel_cell",
            "type": "electric",
            "cost_file_components": {
                "store_tech": "hydrogen storage tank type 1 including compressor",
                "charge_tech": "electrolysis",
                "discharge_tech": "fuel cell",
            },
        },
        {
            "name": "hydrogen_chp",
            "type": "thermal",
            "cost_file_components": {
                "store_tech": "hydrogen storage tank type 1 including compressor",
                "charge_tech": "electrolysis",
                "discharge_tech": "central hydrogen CHP",
            },
            "cop_link_groups": [
                ["central heat pump"],
            ],
            # H2 CHP 热侧供给折算到“减少热泵用电”，采用 CHP 的热效率（efficiency2）。
            "thermal_output_link_keywords": ["h2", "chp"],
        },
        {
            "name": "hot_water",
            "type": "thermal",
            "cost_file_components": {
                "store_tech": "decentral water tank storage",
                "charge_tech": "water tank charger",
                "discharge_tech": "water tank discharger",
                "include_power_capex": False,
            },
            "cop_link_groups": [
                ["decentral heat pump"],
            ],
            "discharge_value_requires_link_keywords": ["decentral heat pump"],
        },
    ]

    if not Path(nc_file_path).exists():
        raise FileNotFoundError(
            f"未找到网络文件：{nc_file_path}\n"
            "请确认结果目录已生成，或修改为你的 `.nc` 绝对/相对路径。"
        )
    # 价格改为直接由 .nc 导出（与 export_reconstructed_prices.py 口径一致）。
    from export_reconstructed_prices import export_prices

    export_prices(
        network_path=nc_file_path,
        baseline_network_path=None,
        out_csv=segmented_price_file,
        provinces=[target_bus],
        week_freq="W-SUN",
        import_agg="min_offer",
        line_cong_eps_mw=1e-3,
        min_inflow_mw=1e-3,
        price_mode="marginal",
        calibrate_with_baseline_max=False,
        currency="CNY",
        fx_cny_per_eur=7.8,
    )

    df_result = evaluate_storage_cycles(
        nc_file_path=nc_file_path,
        target_bus=target_bus,
        tech_configs=tech_configs,
        costs_csv_path=costs_csv_path,
        segmented_price_file=segmented_price_file,
        cny_per_eur=7.8,
    )
    value_by_cycle = (
        df_result.groupby("cycle")["unit_benefit"]
        .first()
        .reindex(list(WINDOWS.keys()))
        .rename("value")
    )
    print("\nValue by cycle (shared across technologies):")
    print(value_by_cycle.to_string())

    print("\nCost comparison by technology and cycle:")
    print(
        df_result[["cycle", "tech", "unit_benefit", "unit_cost_total"]]
        .sort_values(["cycle", "tech"])
        .to_string(index=False)
    )
