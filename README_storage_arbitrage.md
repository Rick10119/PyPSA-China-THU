# 储能套利评估说明（`scripts/evaluate_storage_cycles.py`）

本文档对应当前脚本版本，重点说明多技术套利的折算方法、成本口径与绘图输出。

## 1. 核心设计

脚本在不做逐时 SOC 优化的前提下，使用 `chunk + top/bottom H` 近似法，评估不同技术在日/周/月/年四个循环窗口下的套利潜力。

已支持的技术配置（默认）：

- `battery`
- `hydrogen_fuel_cell`
- `hydrogen_chp`
- `hot_water`

## 2. 数据来源与口径

### 2.1 电价与时序

- 电价序列：`n.buses_t.marginal_price[target_bus]`
- 时间长度：跟随网络 snapshots（通常 8760）

### 2.2 成本与效率（统一成本文件驱动）

所有技术的投资参数和效率默认从 `data/costs/costs_YYYY.csv` 读取（`YYYY` 从 `nc_file_path` 推断，找不到时回退 `2025`）。

读取并年化的参数包括：

- `investment`
- `lifetime`
- `FOM`
- `discount rate`（若缺省，默认 0.04）
- `efficiency`

年化公式与 `add_electricity.py` 一致：

\[
capital\_cost = \left(annuity(lifetime, discount\ rate) + FOM/100\right)\times investment
\]

单位换算：

- `EUR/kW-year -> EUR/MW-year`（乘 1000）
- `EUR/kWh-year -> EUR/MWh-year`（乘 1000）

## 3. H（循环时长）规则

默认规则：

- `battery`：日 6，周 6，月 168，年 168
- `hydrogen_fuel_cell`：日 6，周 24，月 168，年 168（按 electric/tech 规则）
- `hydrogen_chp`：日 6，周 24，月 168，年 168
- `hot_water`：日 6，周 24，月 73，年 73

## 4. 价值折算逻辑

## 4.1 electric 技术（battery、hydrogen_fuel_cell）

在每个 chunk 内：

- 放电价值：`price` 取 `nlargest(H)`
- 充电成本：`price` 取 `nsmallest(H)`
- 收益成本修正：乘/除对应效率

## 4.2 thermal 技术（hydrogen_chp、hot_water）

### 4.2.1 COP 折算（从 nc 读取）

thermal 的等效电价定义：

\[
p^{eq}_t = \frac{p_t}{COP_t}
\]

其中 `COP_t` 来自 `n.links_t.efficiency` 的热泵时变效率（可多链路加权）。

### 4.2.2 放热价值门槛

thermal 放热价值只在以下条件满足时有效：

- 放热设备运行门槛（若配置）
- 热需求门槛（对应热母线负荷 > 0）

否则该小时放热价值强制为 0。

### 4.2.3 H2 CHP 的热侧折算

`hydrogen_chp` 放热端按 CHP 的热效率（`efficiency2`）作为热输出系数，再与 `price/COP` 折算为“减少热泵用电”的等效电侧价值。

即：

\[
Revenue^{thermal}_c = \sum_{t\in Top_H(p^{eq})} p^{eq}_t \times \eta_{heat}
\]

其中 \(\eta_{heat}\) 来自 H2 CHP 的热侧效率。

## 5. 年度成本与净收益

每个周期窗口下：

- `annual_cost_store = c_store_annual * H`
- `annual_cost_power = c_power_annual`
- `annual_cost = annual_cost_store + annual_cost_power`
- `annual_net = annual_gross - annual_cost`

并输出：

- `annual_gross / annual_cost_* / annual_net`
- `unit_benefit / unit_cost_energy / unit_cost_capex / unit_cost_total`

## 6. 图表输出（仅保存，不 show）

默认保存到：

`<nc_file>/../../plots/`

输出文件：

- `shandong_multi_tech_annual_net.png`（多技术年度净收益分组柱状图）
- `shandong_unit_cost_multi_tech.png`（同图：统一效益柱 + 多技术单位总成本曲线）

## 7. 说明

- 本方法用于快速比较技术与周期窗口，不替代严格的逐时 SOC 可行性优化。
- 若需新增技术，优先通过 `tech_configs` 的 `cost_file_components` 配置接入。
