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

- 储能容量情景：`storage-x1`，即 `storage_capacity_guard.target_capacity_multiplier = 1.0`。
- 火电灵活性价格后处理：`threshold_0`，即火电/同步机组低出力最小出力阈值为 `0`，不额外施加 low-output zero-price mask。

所有容量扩张情景在 2025-2050 年包含同步机组出力约束：

```text
本省同步机组电力出力 >= 10% × 本省 AC 电力负荷
```

注意这里的 10% 是本省 AC 电力负荷的 10%，不是所有发电出力的 10%。该约束在 2050 年之后解除，因此 2055/2060 年的光伏 value factor 可能进一步下降，反映更高新能源占比和更少同步机组支撑下的价格稀释。

## 3. 储能容量敏感性

储能敏感性是完整模型重跑。做法是缩放 `storage_capacity_guard.target_capacity_multiplier`，每个倍率生成独立 config 和结果目录。

目录/命名：

- `storage-x0p5`：储能容量目标为 core 的 0.5 倍。
- `storage-x1`：core 储能容量目标。
- `storage-x1p5`：储能容量目标为 core 的 1.5 倍。
- `storage-x2`：储能容量目标为 core 的 2 倍。

汇总文件：

```text
results/storage_availability_sensitivity_summary/storage_availability_national_summary.csv
results/storage_availability_sensitivity_summary/storage_availability_sensitivity_summary.xlsx
```

关键结果（全国发电量加权 solar value factor）：

| year | storage 0.5x | storage 1x/core | storage 1.5x | storage 2x |
|---:|---:|---:|---:|---:|
| 2050 | 0.457 | 0.584 | 0.688 | 0.724 |
| 2060 | 0.444 | 0.569 | 0.657 | 0.700 |

解释：储能容量越高，光伏发电更容易跨时段转移，弃光率下降，solar value factor 上升。

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

当前低出力参考出力使用 `synchronous_generation_floor` 的同步机组全集，因此包括煤电、煤 CC、核电、燃气、CHP、生物质等。mapped 价格曲线本身仍使用热电报价栈。

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

解释：threshold 越高，越多低出力小时被视为 must-run/zero-price 时段，光伏出力更容易落在零价小时，因此 value factor 下降。2050 年之后同步机组出力约束解除，系统中同步机组支撑减少，光伏 value factor 在高新能源占比情景下进一步下降。

为什么 `threshold_0p1` 在 2025-2050 年通常和 `threshold_0` 很接近：这些年份的容量扩张/调度结果本身已经施加了同步机组出力底线，即同步机组出力至少约为本省 AC 电力负荷的 10%。这个模型内生约束通常已经高于或接近后处理中的 `0.1 × 当日最大同步机组参考出力` 低出力阈值，所以额外的 `threshold_0p1` zero-price mask 很少再新增小时。2055/2060 年解除同步机组出力底线后，`threshold_0p1` 才会明显低于 `threshold_0`。

## 5. 推荐阅读结果的顺序

1. 先看 `storage-x1 + threshold_0`，作为 core solar value。
2. 再看储能敏感性 `storage-x0p5 / x1 / x1p5 / x2`，判断储能容量对 solar value 的影响。
3. 最后看火电灵活性 `threshold_0 / 0p1 / 0p2 / 0p3 / 0p4`，判断低出力 must-run/zero-price 假设对 solar value 的影响。
