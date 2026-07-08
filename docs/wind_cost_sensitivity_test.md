# 风电降本敏感性测试说明

目的：检查当风电资本成本低于光伏时，模型是否会进一步增加风电装机，以及风电是否已经受 `wind_capacity_guard` 上限约束。

## 1. 当前 core 风光成本与装机状态

基准情景：`results/version-0621.1H.3-storage-x1/`。

已生成诊断文件：

```text
results/wind_solar_cost_sensitivity_baseline.csv
results/wind_solar_cost_sensitivity_baseline.png
```

关键结果：

| year | carrier | built / target | built / guard upper | annual capital cost |
|---:|---|---:|---:|---:|
| 2050 | solar | 0.754 | 0.580 | 34,531 EUR/MW/yr |
| 2050 | onwind | 1.107 | 0.851 | 38,212 EUR/MW/yr |
| 2050 | offwind | 0.784 | 0.603 | 110,932 EUR/MW/yr |
| 2060 | solar | 0.708 | 0.544 | 29,684 EUR/MW/yr |
| 2060 | onwind | 1.061 | 0.816 | 32,590 EUR/MW/yr |
| 2060 | offwind | 0.764 | 0.588 | 92,553 EUR/MW/yr |

解释：

- 当前陆风年化资本成本略高于光伏，海风显著高于光伏。
- 陆风已经超过 national target，但尚未达到 `target_upper_multiplier = 1.3` 对应的 guard upper bound。
- 海风离上限更远。
- 因此，风电降本测试仍有意义：如果风电变便宜，模型理论上还有增加风电装机的空间，尤其是陆风。

## 2. 新增 wind-cheap 测试配置

这组三个 case 同时降低风电资本成本、放宽风电容量上限。光伏资本成本保持 core，电池、H2、Sabatier 等其他成本也保持 core。

| case | config | wind cost factor | wind guard upper multiplier |
|---|---|---:|---:|
| `wind_cheap_x0p8` | `configs/wind_cost_sensitivity/config_wind_cheap_x0p8.yaml` | 0.8 | 1.5 |
| `wind_cheap_x0p6` | `configs/wind_cost_sensitivity/config_wind_cheap_x0p6.yaml` | 0.6 | 2.0 |
| `wind_cheap_x0p4` | `configs/wind_cost_sensitivity/config_wind_cheap_x0p4.yaml` | 0.4 | 2.5 |

case 清单：

```text
configs/wind_cost_sensitivity/wind_cost_sensitivity_cases.csv
```

以 `wind_cheap_x0p8` 为例，相对 core 的变化：

```yaml
version: 0621.1H.3-wind-cheap-x0p8

wind_capacity_guard:
  target_upper_multiplier: 1.5

aluminum:
  scenario_dimensions:
    market_opportunity:
      mid:
        vre_cost_factor: 1.0
        solar_cost_factor: 1.0
        wind_cost_factor: 0.8
        battery_cost_factor: 1.0
        h2_cost_factor: 1.0
        sabatier_cost_factor: 1.0
```

含义：

- 光伏资本成本保持 core：`solar_cost_factor = 1.0`。
- 陆风和海风资本成本分别降为 core 的 80% / 60% / 40%。
- 风电 guard upper bound 分别放宽到 target 的 1.5 / 2.0 / 2.5 倍。
- 电池、H2、Sabatier 等其他成本保持 core。

按当前 core 成本粗略换算，风电降本后陆风会低于光伏：

| year | solar cost | onwind 0.8x | onwind 0.6x | onwind 0.4x |
|---:|---:|---:|---:|---:|
| 2050 | 34,531 | 30,570 | 22,927 | 15,285 |
| 2060 | 29,684 | 26,072 | 19,554 | 13,036 |

## 3. 运行方式

成本调整不是只改 2050。Snakemake 每个规划年会读取对应的 `data/costs/costs_<year>.csv`，`wind_cost_factor` 会在 `load_costs()` 之后应用到该规划年的 `onwind/offwind` capital cost。因此 2030、2035、2040、2045、2050、2055、2060 都会按同一个风电成本倍数调整。

一键运行三个 case，并在跑完后自动填充 solar value dataset、输出风光装机/成本诊断、生成相对 core 的 solar value factor 对比：

```bash
conda run -n pypsa python scripts/run_wind_cost_sensitivity.py --cores 32
```

如果三个模型已经跑完，只想重新汇总/画图：

```bash
conda run -n pypsa python scripts/run_wind_cost_sensitivity.py --skip-snakemake
```

完整模型也可以分别运行三个 config：

```bash
conda run -n pypsa snakemake --configfile configs/wind_cost_sensitivity/config_wind_cheap_x0p8.yaml --cores 32
conda run -n pypsa snakemake --configfile configs/wind_cost_sensitivity/config_wind_cheap_x0p6.yaml --cores 32
conda run -n pypsa snakemake --configfile configs/wind_cost_sensitivity/config_wind_cheap_x0p4.yaml --cores 32
```

如果在集群上跑，可以按现有 job 模板创建一个对应的 slurm job，核心是使用上面的 config file。

## 4. 跑完后的诊断

跑完后先检查风光装机与成本：

```bash
conda run -n pypsa python scripts/summarize_wind_solar_cost_capacity.py \
  --config configs/wind_cost_sensitivity/config_wind_cheap_x0p8.yaml \
  --output-csv results/wind_solar_cost_sensitivity_wind_cheap_x0p8.csv \
  --output-png results/wind_solar_cost_sensitivity_wind_cheap_x0p8.png

conda run -n pypsa python scripts/summarize_wind_solar_cost_capacity.py \
  --config configs/wind_cost_sensitivity/config_wind_cheap_x0p6.yaml \
  --output-csv results/wind_solar_cost_sensitivity_wind_cheap_x0p6.csv \
  --output-png results/wind_solar_cost_sensitivity_wind_cheap_x0p6.png

conda run -n pypsa python scripts/summarize_wind_solar_cost_capacity.py \
  --config configs/wind_cost_sensitivity/config_wind_cheap_x0p4.yaml \
  --output-csv results/wind_solar_cost_sensitivity_wind_cheap_x0p4.csv \
  --output-png results/wind_solar_cost_sensitivity_wind_cheap_x0p4.png
```

一键脚本会把所有 case 的结果汇总到：

```text
results/wind_cost_sensitivity_summary/wind_cost_solar_value_factor_comparison.csv
results/wind_cost_sensitivity_summary/wind_cost_solar_value_factor_comparison.png
results/wind_cost_sensitivity_summary/wind_cost_capacity_comparison.csv
results/wind_cost_sensitivity_summary/wind_cost_sensitivity_summary.xlsx
```

重点看：

- `built_vs_guard_upper` 是否接近 1.0；若接近 1.0，说明该技术已经按 guard upper bound 建满。
- `onwind` 是否从当前 2050 的 0.851、2060 的 0.816 进一步接近 1.0；注意每个 case 的 1.0 对应不同 guard upper multiplier。
- `offwind` 是否仍低于上限；若仍低，说明即使 0.8× 后海风仍不是主要边际扩张选项。
- solar value 是否因更多风电替代/挤出光伏而改变。

如需更新 solar value dataset，运行完整模型后再执行现有 `fill_solar_value_dataset_2025.py` 流程或 Snakemake 对应目标。
