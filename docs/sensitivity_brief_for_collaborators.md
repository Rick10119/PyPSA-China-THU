# 储能与火电灵活性敏感性分析简要说明

本文用于说明当前光伏价值因子（solar value factor）结果的核心情景、储能容量敏感性、火电灵活性敏感性，以及结果文件/字段命名方式。

## 1. Solar value 指标命名

所有情景的核心输出都是 `solar_value_dataset.xlsx`。主要字段含义如下：

- `solar_ele_GWh`：该省该年的光伏发电量。
- `value_factor_numerator`：光伏发电加权平均电价，即 PV weighted average price。
- `value_factor_denominator`：全系统发电加权平均电价，即 system weighted average price。
- `value_factor`：光伏价值因子，计算为 `value_factor_numerator / value_factor_denominator`。
- `solar_penetration`：光伏渗透率。
- `solar_curtailment_rate`：弃光率。
- `solar_capacity_factor`：光伏容量因子。

全国汇总图和汇总表使用 `solar_ele_GWh` 作为权重，对各省 `value_factor` 做发电量加权平均。

## 2. Core 情景定义

当前 core 情景建议理解为：

- 储能情景：`storage-x1`，即 `storage_capacity_guard.target_capacity_multiplier = 1.0` 且电池资本成本为 `1.0x`。
- 火电灵活性价格口径：`threshold_0p4`，即火电/同步机组低出力阈值为当日最大同步机组参考出力的 `40%`，并保留对应 zero-price mask。

所有容量扩张情景在 2025-2055 年包含同步机组出力约束，其中 2025-2050 年为 10%，2055 年降为 5%，2060 年解除：

```text
2025-2050: 本省同步机组电力出力 >= 10% × 本省 AC 电力负荷
2055:      本省同步机组电力出力 >= 5% × 本省 AC 电力负荷
2060:      不施加同步机组出力底线
```

注意这里的比例是本省 AC 电力负荷的比例，不是所有发电出力的比例。2055 年同步机底线放松到 5%，2060 年完全解除，因此后期光伏 value factor 可能进一步下降，反映更高新能源占比和更少同步机组支撑下的价格稀释。

## 3. 储能容量敏感性

储能敏感性是完整模型重跑。做法是联动缩放 `storage_capacity_guard.target_capacity_multiplier` 和电池资本成本，每个组合生成独立 config 和结果目录。所有储能敏感性 workbook 统一用火电灵活性 `threshold_0p4` 价格口径填充，即 `fill_solar_value_dataset_2025.py --allow-zero-price` + `daily_low_output_zero_threshold = 0.4`。

目录/命名：

- `storage-x0p7`：储能容量目标为 core 的 0.7 倍，电池成本为 1.0 倍。
- `storage-x1`：core 储能容量目标，电池成本为 1.0 倍。
- `storage-x1p5`：储能容量目标为 core 的 1.5 倍，电池成本为 1.0 倍。
- `storage-x2`：储能容量目标为 core 的 2 倍，电池成本为 1.0 倍。

汇总文件：

```text
results/storage_availability_sensitivity_summary/storage_availability_national_summary.csv
results/storage_availability_sensitivity_summary/storage_availability_sensitivity_summary.xlsx
```

关键结果（全国发电量加权 solar value factor）需在新一组 `0.7 / 1.0 / 1.5 / 2.0` 容量、统一 `1.0x` 成本场景重跑后更新。

解释：该敏感性只改变储能可用容量，电池成本统一保持 `1.0x`。储能容量越高，光伏发电更容易跨时段转移，弃光率下降，solar value factor 上升。

## 4. 火电灵活性敏感性

火电灵活性敏感性不是完整重跑容量扩张模型，而是基于 `storage-x1` 已求解网络做价格后处理。它以 planning marginal price 为基础，针对不同最小出力阈值生成 zero-price mask，然后重新填充 `solar_value_dataset.xlsx`。

目录/命名：

- `threshold_0`：最小出力阈值为 0；core 火电灵活性价格口径。
- `threshold_0p1`：最小出力阈值为 0.1。
- `threshold_0p2`：最小出力阈值为 0.2。
- `threshold_0p3`：最小出力阈值为 0.3。
- `threshold_0p4`：最小出力阈值为 0.4。
- `threshold_0_lmp`：纯 planning LMP 参考线。
- `threshold_0_sync`：阈值 0 + 同步机底线 zero mask 参考线。

低出力 zero-price mask 的含义：

```text
若本省同步机组参考出力 < 当日最大同步机组参考出力 × threshold，则该省该小时 mapped price 置 0
```

当前低出力参考出力使用 `synchronous_generation_floor` 的同步机组全集，因此包括煤电、煤 CC、核电、燃气、CHP、生物质，以及接在 AC 电力母线上的 `hydroelectricity` 水电；`hydro_inflow` 和抽蓄 `PHS` 不计入。mapped 价格曲线本身仍使用热电报价栈。

汇总文件：

```text
results/version-0621.1H.3-storage-x1/thermal_flexibility_sensitivity/thermal_flexibility_value_factor_summary.csv
```

关键结果（全国发电量加权 solar value factor）：

| case | min-output threshold | 2050 | 2055 | 2060 |
|---|---:|---:|---:|---:|
| `threshold_0` | 0.0 | 0.584 | 0.584 | 0.569 |
| `threshold_0p1` | 0.1 | 0.584 | 0.424 | 0.447 |
| `threshold_0p2` | 0.2 | 0.557 | 0.396 | 0.427 |
| `threshold_0p3` | 0.3 | 0.517 | 0.372 | 0.411 |
| `threshold_0p4` | 0.4 | 0.467 | 0.354 | 0.401 |

解释：threshold 越高，越多低出力小时被视为 must-run/zero-price 时段，光伏出力更容易落在零价小时，因此 value factor 下降。2055 年同步机组出力底线降为 7.5%，2060 年进一步降为 5%，系统中同步机组支撑减少，光伏 value factor 在高新能源占比情景下进一步下降。

为什么 `threshold_0p1` 在 2025-2050 年通常和 `threshold_0` 很接近：这些年份的容量扩张/调度结果本身已经施加了同步机组出力底线，即同步机组出力至少约为本省 AC 电力负荷的 10%。这个模型内生约束通常已经高于或接近后处理中的 `0.1 × 当日最大同步机组参考出力` 低出力阈值，所以额外的 `threshold_0p1` zero-price mask 很少再新增小时。2055 年底线降为 7.5%、2060 年降为 5% 后，`threshold_0p1` 才会明显低于 `threshold_0`。

## 5. 推荐阅读结果的顺序

1. 先看 `storage-x1 + threshold_0p4`，作为 core solar value。
2. 再看储能敏感性 `storage-x0p7 / x1 / x1p5 / x2`，判断储能容量对 solar value 的影响。
3. 最后看火电灵活性 `threshold_0 / 0p1 / 0p2 / 0p3 / 0p4`，判断低出力 must-run/zero-price 假设对 solar value 的影响。
