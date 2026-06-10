# PyPSA-China 中文说明

本仓库基于 PyPSA 构建中国省级能源系统优化模型，覆盖电力、供热、气、煤、氢等部门，并包含电解铝灵活性与电价仿真相关模块。当前 `price-simulation` 分支的重点是：

- 多年 myopic 扩建与运行优化；
- 固定装机后的分段报价 dispatch-only 电价仿真；
- 省级同步机组最小出力约束；
- 风电、光伏、核电与储能扩张约束；
- 电解铝负荷灵活性场景与后处理。

## 快速运行

先创建并激活环境：

```bash
conda env create -f envs/environment.yaml
conda activate pypsa
```

运行主流程：

```bash
snakemake --cores 6
```

如果只想先检查规则和输入输出：

```bash
snakemake -np
```

大型情景建议使用 SLURM：

```bash
python scripts/generate_slurm_jobs_advanced.py
./submit_multiple_jobs.sh
```

## 关键配置

所有主要开关在 `config.yaml` 中。

基础设置：

```yaml
foresight: "myopic"
baseyear: 2025
freq: "1h"
scenario:
  planning_horizons: [2025, 2030, 2035, 2040, 2045, 2050, 2055, 2060]
```

电解铝模块：

```yaml
add_aluminum: true
iterative_optimization: true
aluminum_commitment: false
aluminum_max_iterations: 10
aluminum_convergence_tolerance: 0.01
aluminum_capacity_ratio: 1.0
```

二阶段电价仿真：

```yaml
dispatch_segmented_prices:
  enabled: true
  export_prices: true
```

同步机组最小出力约束：

```yaml
synchronous_generation_floor:
  enabled: true
  ratio: 0.10
```

风电、光伏、核电、储能扩张约束分别由以下配置块控制：

- `wind_capacity_guard`
- `solar_capacity_guard`
- `nuclear_capacity_guard`
- `storage_capacity_guard`

## 主流程

Snakemake 工作流的核心顺序为：

```text
prepare_base_networks_2020
  -> prepare_base_networks
  -> add_existing_baseyear
  -> add_brownfield
  -> solve_network_myopic
  -> run_dispatch_segmented
  -> export_dispatch_segmented_prices
```

其中 `solve_network_myopic` 负责扩建与运行优化；`run_dispatch_segmented` 在固定装机后重算全年调度，并输出用于电价分析的结果。

规则名 `prepare_base_networks_2020` 是历史遗留：它现在对应 `scenario.planning_horizons` 的**第一个年份**（当前为 `2025`），而不是固定的 `2020`。

### 基础年的既有省间输电容量

首个规划年规则会把 `data/grids/edges_current.csv` 作为 `edges_ext` 传给
`scripts/prepare_base_network.py`。脚本读取第三列（MW）并添加不可扩建的
`ext positive` / `ext reversed` 线路；随后仍从 `data/grids/edges.txt` 添加可扩建的
`positive` / `reversed` 线路。

示例：

```text
Gansu,Xinjiang,3000
```

如果**升级基础年**（修改 `baseyear` 或 `planning_horizons[0]`），通常还需要同步检查并更新：

- `config.yaml` 中的 `baseyear` 与 `scenario.planning_horizons`
- `data/grids/edges_current.csv`（新基准年的既有 AC 输电容量）
- `data/existing_infrastructure/` 等 brownfield 相关输入

若不更新 `edges_current.csv`，模型会把本应固定的跨省走廊当成从 0 开始自由扩建的线路，可能严重低估既有输电能力。

## 电价输出

主输出来自 PyPSA 求解后的省级 AC bus 边际电价：

- 字段：`n.buses_t.marginal_price`
- 导出脚本：`scripts/export_reconstructed_prices.py`
- 默认单位：`CNY/MWh`
- 默认汇率：`fx_cny_per_eur: 7.8`

二阶段 dispatch 输出路径通常为：

```text
results/version-<version>/prices/dispatch_segmented/<heating_demand>/
```

`*_mapped.csv` 是额外的 mapped-price sidecar，主要用于光伏 value factor 等后处理。它不是原始 LMP，而是结合本地同步/火电出力、燃料价和低出力/必开 floor 规则后的分析口径；各省独立映射，不单独叠加省间输电价格修正。

mapped 价格按以下顺序重建：

1. 根据本地同步/火电出力和燃料报价曲线构建 mapped 价格；双周归一化分母为
   `max(双周最大火电出力, 双周最小火电出力 / lr_threshold_first)`。
2. 低出力零价：若某时刻省内火电出力低于
   `分组最大火电出力 × (1 + 备用裕度) × 阈值`，则 mapped 价格置 `0`。
   分组最大火电出力在 `low_output_reference_freq` 窗口内取最大值（默认 `W-SUN`，即按周日至周六的一周）；
   `low_output_reserve_margin`（默认 `0.10`）在周峰值之上预留备用空间，使相对“峰值+备用”偏低的时段视为必开而非边际定价；
   `daily_low_output_zero_threshold`（默认 `0.4`）可全局或按年设置。
   若省略上述配置，导出脚本回退为旧的按日窗口、无备用裕度行为。
3. 必开 floor：同步出力接近本地 AC 负荷 `10 %` 时视为必开；在 `1.5 ×` floor 带宽内（默认即负荷的 `15 %`）mapped 价格保持 `0`，且不受后续煤电底价抬升影响。

相关配置位于 `dispatch_segmented_prices.price_export`：

```yaml
dispatch_segmented_prices:
  price_export:
    week_freq: "2W-SUN"
    thermal_load_floor:
      enabled: true
      ratio: 0.10
    daily_low_output_zero_threshold: 0.4
    low_output_reference_freq: "W-SUN"
    low_output_reserve_margin: 0.10
    mapped_supply_curve:
      lr_threshold_first: 0.4
```

## 数据目录

常用输入数据包括：

- `data/costs/costs_<year>.csv`：技术成本与燃料成本；
- `data/grids/edges_current.csv`：首个规划年的既有 AC 省间输电容量（MW，第三列）；
- `data/grids/edges.txt`：可扩建 AC/H2 走廊拓扑；
- `data/existing_infrastructure/*_capacity.csv`：已有装机；
- `data/load/load_<year>_weatheryears_1979_2016_TWh.h5`：省级电力负荷；
- `data/heating/heat_demand_profile_<scenario>_<year>.h5`：供热负荷；
- `data/p_nom/al_smelter_p_max.csv`：电解铝最大功率；
- `data/aluminum_demand/aluminum_demand_all_scenarios.json`：电解铝需求情景；
- `resources/profile_onwind.nc`、`resources/profile_offwind.nc`、`resources/profile_solar.nc`：可再生出力 profile。

更完整的数据说明见 `data/README.md`。

## 后处理脚本

常用脚本：

- `scripts/plot_value_scenario_comparison.py`：情景价值/成本对比；
- `scripts/plot_capacity_MMM_2050.py`：2050 年 MMM 情景容量与成本图；
- `scripts/plot_optimal_point.py`：不同年份、市场和灵活性下的最优点；
- `scripts/plot_shandong_price_vs_thermal.py`：山东电价与火电出力诊断图；
- `scripts/plot_solar_value_factor_yearly.py`：光伏 value factor 年度图；
- `scripts/fill_solar_value_dataset_2025.py`：补全 2025 光伏 value dataset。

## 文档

- `docs/aluminum_integration_guide.md`：电解铝模块说明；
- `docs/README_aluminum_iterative.md`：迭代优化说明；
- `docs/scenario_dimensions_guide.md`：情景维度说明；
- `docs/price_module_market_clearing_report.md`：电价模块与市场出清报告；
- `docs/README_solar_value_dataset_2025.md`：光伏 value dataset 说明；
- `docs/slurm_jobs_guide.md`：SLURM 任务说明。

## 合并到 main 前建议

建议保留：

- 主工作流、配置、数据命名规范化改动；
- `run_dispatch_segmented_prices.py`、`export_reconstructed_prices.py` 和相关 price 配置；
- 风/光/核/储容量 guard；
- 与论文复现直接相关的绘图和汇总脚本。

建议归档或移出主线：

- 一次性诊断脚本，如 `scripts/_diag_oct30_thermal.py`、`scripts/_diag_shandong_daily.py`；
- 只服务某次服务器运行的 job 模板；
- 一次性数据生成脚本，除非 README 或论文复现流程明确需要它们；
- 本地系统专用脚本，如 `run_local.ps1`。

## 环境提示

仓库脚本与 PyPSA 版本配套。若使用过新的 PyPSA 版本，可能遇到：

```text
ImportError: cannot import name 'Dict' from pypsa.descriptors
```

优先使用 `envs/environment.yaml` 创建环境；论文复现建议使用 Gurobi。
