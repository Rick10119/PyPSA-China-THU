# 火电灵活性与储能容量限制敏感性分析

本文档说明两类用于评估 `fill_solar_value_dataset_2025.py` 结果稳健性的敏感性分析：

- 火电灵活性敏感性：改变价格重构和后处理中的火电最小出力阈值。
- 储能容量限制敏感性：改变 `storage_capacity_guard` 中储能可用容量目标。

两者的计算对象都是 `solar_value_dataset.xlsx` 中的光伏价值因子、光伏渗透率、弃光率、容量因子等指标，但实现方式不同。

## 1. 火电灵活性敏感性

脚本：

```bash
scripts/run_thermal_flexibility_sensitivity.py
```

用途：

- 这是后处理敏感性分析。
- 不重新求解 PyPSA 容量扩张模型。
- 默认复用储能容量限制基准 case `storage-x1` 对应的已求解 `postnetwork` 和 `dispatch_segmented` 网络。
- 以 planning marginal price 为基准；对不同火电**最小出力阈值**生成 zero-price mask，并仅将满足条件的省份/小时价格置零。
- 再用 `fill_solar_value_dataset_2025.py --allow-zero-price` 重新填充 `solar_value_dataset.xlsx`。
- 阈值 **> 0** 时，置零条件为 **同步机 10% 本地负荷底线**（`synchronous_generation_floor`，**仅 2025–2050**）**与**最小出力阈值（`daily_low_output_zero_threshold`）的并集；不启用 `thermal_load_floor`。
- **低出力置零**：当本省同步机组参考出力低于 **当日最大同步机组参考出力 × 阈值**（`low_output_reference_freq: D`，无额外 reserve 裕度）时标记置零；例如 `0.1` 表示低于日最大值的 10% 才置零。默认 `low_output_carrier_scope: synchronous_generation_floor`，因此核电和生物质会计入低出力判定；mapped 价格曲线本身仍使用 `mapped_carriers` 的热电报价栈。
- 同步机置零 mask 与 dispatch 约束对齐：`sync >= ratio×负荷 - sync_floor_slack_mw`；后处理仅在同步出力贴近该 RHS（默认 +1 MW 带宽）时标记置零。**2051 年及以后不再施加同步机置零。**
- 填充 workbook 时，置零 mask **仅在省内有光伏出力的小时**生效（`--zero-mask-only-when-solar-generates`），避免夜间置零抬高 value factor、破坏单调性。
- **主曲线 `threshold_0/`**：最小出力阈值 0、**不**含同步机 mask（灵活性端点；与 0.1–0.4 同走 `--allow-zero-price` 流程）。
- **参考曲线 `threshold_0_lmp/`**：`--planning-marginal` 纯 LMP，与储能 `storage-x1` 一致。
- **参考曲线 `threshold_0_sync/`**：阈值 0 + 仅同步机底线（2025–2050）。

默认阈值：

```text
0.4, 0.3, 0.2, 0.1, 0.0
```

默认还会额外生成 `threshold_0_sync/` 与 `threshold_0_lmp/`（storage-x1 纯 LMP 参考）。分别用 `--skip-zero-sync-floor` / `--skip-pure-lmp-reference` 关闭。

运行命令：

```bash
python scripts/run_thermal_flexibility_sensitivity.py
```

默认情况下，如果存在下面这个 config，脚本会使用它作为火电灵活性敏感性的基准：

```text
configs/storage_availability_sensitivity/config_storage_x1.yaml
```

也就是说，火电灵活性敏感性默认基于：

```text
results/version-<base-version>-storage-x1/
```

如果想改用原始 `config.yaml` 或其他 case，可以显式传入：

```bash
python scripts/run_thermal_flexibility_sensitivity.py --config config.yaml
```

或：

```bash
python scripts/run_thermal_flexibility_sensitivity.py \
  --config configs/storage_availability_sensitivity/config_storage_x0p5.yaml
```

指定阈值：

```bash
python scripts/run_thermal_flexibility_sensitivity.py \
  --threshold 0.4 \
  --threshold 0.2 \
  --threshold 0.0
```

如果当前版本目录下没有可用的 `solar_value_dataset.xlsx` 模板，可以显式指定：

```bash
python scripts/run_thermal_flexibility_sensitivity.py \
  --template-workbook results/version-0605.1H.2/solar_value_dataset.xlsx
```

默认输出位置：

```text
results/version-<storage-x1版本>/thermal_flexibility_sensitivity/
```

每个阈值一个子目录：

```text
threshold_0p4/
threshold_0p3/
threshold_0p2/
threshold_0p1/
threshold_0/          # 主曲线端点：无 sync / 无低出力 mask
threshold_0_sync/     # 参考：0 + 同步机底线（2025–2050）
threshold_0_lmp/      # 参考：纯 LMP（storage-x1）
```

每个阈值目录中主要包括：

```text
solar_value_dataset.xlsx
mapped_prices/    # 除 threshold_0_lmp 外均有
figures/
```

主曲线应满足 **value factor(0) ≥ value factor(0.1) ≥ … ≥ value factor(0.4)**（阈值越高，置零越多，光伏价值因子越低）。`threshold_0_lmp` 与 `threshold_0_sync` 为参考线，不参与该单调序列。

总汇总输出：

```text
thermal_flexibility_value_factor_summary.csv
thermal_flexibility_value_factor_comparison.png
thermal_flexibility_value_factor_comparison.pdf
```

## 2. 储能容量限制敏感性

脚本：

```bash
scripts/run_storage_availability_sensitivity.py
scripts/summarize_storage_availability_sensitivity.py
```

用途：

- 这是完整模型重跑敏感性分析。
- 通过 `storage_capacity_guard.target_capacity_multiplier` 缩放储能可用容量目标。
- 储能约束使用严格上限：`target_lower_multiplier = 0.0`，`target_upper_multiplier = 1.0`，即允许少建，但不允许超过倍率后的目标容量。
- 每个倍率生成独立 config 和独立 results version。
- Snakemake 跑完后自动调用 `fill_solar_value_dataset_2025.py` 填充该倍率对应的 `solar_value_dataset.xlsx`。

默认倍率：

```text
0.5, 1.0, 1.5, 2.0
```

含义：

```text
0.5x = 储能容量严格上限为当前目标的 50%
1.0x = 储能容量严格上限为当前目标
1.5x = 储能容量严格上限为当前目标的 150%
2.0x = 储能容量严格上限为当前目标的 200%
```

### 2.1 生成 config 和 Slurm 作业

只生成文件，不提交、不运行：

```bash
python scripts/run_storage_availability_sensitivity.py --skip-plot
```

生成的 config：

```text
configs/storage_availability_sensitivity/config_storage_x0p5.yaml
configs/storage_availability_sensitivity/config_storage_x1.yaml
configs/storage_availability_sensitivity/config_storage_x1p5.yaml
configs/storage_availability_sensitivity/config_storage_x2.yaml
```

生成的 Slurm 作业：

```text
jobs_storage_availability/job_storage_x0p5.slurm
jobs_storage_availability/job_storage_x1.slurm
jobs_storage_availability/job_storage_x1p5.slurm
jobs_storage_availability/job_storage_x2.slurm
```

生成的 manifest：

```text
configs/storage_availability_sensitivity/storage_availability_cases.csv
```

### 2.2 提交到 Slurm

生成并提交四个作业：

```bash
python scripts/run_storage_availability_sensitivity.py --skip-plot --submit
```

也可以手动提交单个作业：

```bash
sbatch jobs_storage_availability/job_storage_x0p5.slurm
sbatch jobs_storage_availability/job_storage_x1.slurm
sbatch jobs_storage_availability/job_storage_x1p5.slurm
sbatch jobs_storage_availability/job_storage_x2.slurm
```

如需强制重跑完整 workflow：

```bash
FORCE_RESTART=1 sbatch jobs_storage_availability/job_storage_x1.slurm
```

### 2.3 本地运行

在本地依次运行四个 case：

```bash
python scripts/run_storage_availability_sensitivity.py --skip-plot --run-local
```

如需使用较少核心：

```bash
python scripts/run_storage_availability_sensitivity.py \
  --skip-plot \
  --run-local \
  --cores 8
```

如果需要指定 workbook 模板：

```bash
python scripts/run_storage_availability_sensitivity.py \
  --skip-plot \
  --run-local \
  --template-workbook results/version-0605.1H.2/solar_value_dataset.xlsx
```

### 2.4 储能敏感性输出位置

默认四个结果目录：

```text
results/version-0621.1H.3-storage-x0p5/
results/version-0621.1H.3-storage-x1/
results/version-0621.1H.3-storage-x1p5/
results/version-0621.1H.3-storage-x2/
```

其中 `0621.1H.3` 来自当前 `config.yaml` 的 `version`。如果以后 base config 版本号改变，输出目录也会相应变成：

```text
results/version-<base-version>-storage-x0p5/
results/version-<base-version>-storage-x1/
results/version-<base-version>-storage-x1p5/
results/version-<base-version>-storage-x2/
```

每个结果目录中主要包括：

```text
postnetworks/
dispatch_segmented/
prices/
solar_value_dataset.xlsx
solar_capacity_compare_by_year.csv
```

### 2.5 汇总储能敏感性结果

四个 case 都跑完后：

```bash
python scripts/summarize_storage_availability_sensitivity.py
```

默认读取：

```text
configs/storage_availability_sensitivity/storage_availability_cases.csv
```

默认汇总输出：

```text
results/storage_availability_sensitivity_summary/
```

主要文件：

```text
storage_availability_national_summary.csv
storage_availability_province_detail.csv
storage_availability_sensitivity_summary.xlsx
storage_availability_value_factor_comparison.png
storage_availability_value_factor_comparison.pdf
```

其中 `storage_availability_value_factor_comparison.png` / `.pdf` 是汇总图：横轴为年份，纵轴为全国发电量加权的光伏 value factor，不同折线对应不同储能容量倍率。

如果只有部分 case 跑完，想先汇总已有结果：

```bash
python scripts/summarize_storage_availability_sensitivity.py --allow-missing
```

## 3. 两类敏感性对比

| 敏感性 | 是否重跑容量扩张 | 改变对象 | 默认 case | 主要输出 |
|---|---:|---|---|---|
| 火电灵活性 | 否 | planning marginal price 的低出力置零阈值 | `0.4, 0.3, 0.2, 0.1, 0.0` | `thermal_flexibility_sensitivity/` |
| 储能容量限制 | 是 | `storage_capacity_guard.target_capacity_multiplier` | `0.5, 1.0, 1.5, 2.0` | `version-*-storage-x*/` |

火电 `threshold_0` 与储能 `storage-x1` 使用相同价格口径（纯 planning LMP）；若两者 value factor 仍有差异，来自 `storage-x1` 完整重跑与火电后处理复用网络之间的容量/调度差别，而非价格 mask。

建议流程：

1. 先确认 base config 已能成功生成 `postnetwork`、`dispatch_segmented` 和 `prices`。
2. 火电灵活性敏感性可先跑，因为它只做后处理，速度较快。
3. 储能容量限制敏感性需要完整重跑模型，建议用 Slurm 并行提交。
4. 两类结果都以 `solar_value_dataset.xlsx` 为核心输出，后续画图或表格比较时优先使用对应的 summary CSV / Excel。
