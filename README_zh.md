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

`*_mapped.csv` 是额外的 mapped-price sidecar，主要用于光伏 value factor 等后处理。它不是原始 LMP，而是结合本地同步/火电出力、燃料价、低出力 floor 规则和省间外送修正后的分析口径。

## 数据目录

常用输入数据包括：

- `data/costs/costs_<year>.csv`：技术成本与燃料成本；
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
