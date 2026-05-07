# PyPSA-China（简化中文说明）

本文件是面向“更新供热需求 + 可选建筑热惯性（热容）+ 运行 heat-only 流程”的简化说明。

## 你最常用的命令

在已激活 conda 环境后运行：

```bash
snakemake --cores 6
```

默认会按当前 `Snakefile` 生成：

- `prenetwork`（含供热部门）
- `heatonly_postnetwork`（供热解耦求解）
- 典型日供热出图
- `summary`（供热占比/装机/CO2）

## 1）只更新热需求（热负荷）要怎么做？

模型会从 HDF5 读取热需求时间序列，典型文件例如：

- `data/heating/heat_demand_profile_positive_2030.h5`

读取规则：

- 优先 key：`/heat_demand_profiles`
- 如果没有这个 key：自动使用文件中的**第一个 key**（会打印 warning）

数据形状要求：

- **index**：时间戳，必须能对齐 `network.snapshots`（由 `config.yaml: freq` 控制，例如 `6h`）
- **columns**：省份名（必须与 `pro_names` 一致，例如 `Tianjin` 等）
- **values**：热负荷功率（通常按 MW_th 理解），用于写入 `Load.p_set`

该热需求会按集中/分散供热比例拆分到：

- `<province> central heat`
- `<province> decentral heat`

你只需要**覆盖同路径的 `.h5` 文件**即可（文件名不变最省事；key 名变了也能自动兜底）。

## 2）建筑热惯性（热容）如何建模？怎么启用？

我们采用“需求侧热容”的简单建模方式：在 **central heat** 和 **decentral heat** 两套热母线上各加一个建筑热容 `Store`。

### 参数 CSV（单文件，集中/分散两套列）

模板文件：

- `data/heating/building_inertia_template.csv`

列定义（每省一行）：

- `province`
- `C_th_MWh_per_K_central`, `deltaT_K_central`, `standing_loss_per_hour_central`
- `C_th_MWh_per_K_decentral`, `deltaT_K_decentral`, `standing_loss_per_hour_decentral`

其中可用热容能量按下面计算：
\[
e\_{nom} = C\_{th}\,[\mathrm{MWh/K}] \times \Delta T\,[\mathrm{K}]
\]

当某一侧的 \(e_{nom}\) 全部为 0 时，该侧的建筑热容 `Store` 不会被添加（保持与原模型一致）。

### 在 `config.yaml` 打开开关

```yaml
building_inertia:
  enabled: true
  params_csv: "data/heating/building_inertia_template.csv"
  carrier: "building thermal mass"
```

## 3）电价（exogenous）在 6h 分辨率下怎么处理？

当 `config.yaml: freq` 不是 `1h`（例如 `6h`，全年 1460 个 snapshot），但你的外部电价 CSV 仍是逐小时 `hour=1..8760` 时，电价读取逻辑会自动把小时电价按 slot 聚合到目标快照长度（默认取均值），避免对齐报错。

## 3.1）电价（endogenous / marginal）如何计算？（推荐口径）

本仓库当前推荐的“电价”口径为 **PyPSA 求解后的边际电价**（locational marginal price, LMP），即每个省级电力母线（AC bus）的能量平衡约束对偶变量：

- **来源字段**：`n.buses_t.marginal_price[<province>]`
- **计算脚本**：`scripts/reconstruct_market_prices.py` 中的 `marginal_retail_prices`
- **导出脚本**：`scripts/export_reconstructed_prices.py`（仅支持 `--price-mode marginal`）

### 输出单位（统一人民币）

模型内部价格量纲为 **EUR/MWh**。导出 CSV 时默认会按汇率转换为 **CNY/MWh**：

- `--currency CNY`（默认）
- `--fx-cny-per-eur 7.8`（默认，可改）

导出示例（山东 2025 全年）：

```bash
conda run -n pypsa python scripts/export_reconstructed_prices.py \
  --network "results/version-0425.1H.1/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2025.nc" \
  --out "results/version-0425.1H.1/_marginal_prices_2025_Shandong.csv" \
  --price-mode marginal \
  --currency CNY \
  --fx-cny-per-eur 7.8 \
  --province Shandong
```

## 3.2）两阶段：规划后固定装机，再跑 8760 dispatch（分段报价）

为了让运行阶段的电价更贴近现货出清（尤其是煤电/气电分段报价），我们支持一个二阶段流程：

1) **规划/全模型求解**得到 `postnetwork-*.nc`（含装机 `p_nom_opt`）  
2) **运行/dispatch-only**：固定装机=`p_nom_opt`，仅重算全年 8760 调度与 **marginal 价格**

对应 Snakemake 规则：

- `run_dispatch_segmented`：输出 `results/version-*/dispatch_segmented/.../postnetwork-dispatch-seg-*.nc`
- `export_dispatch_segmented_prices`：输出 `results/version-*/prices/dispatch_segmented/.../dispatch_segmented_prices-*.csv`（CNY/MWh）

启用开关（`config.yaml`）：

```yaml
dispatch_segmented_prices:
  enabled: true
  export_prices: true
```

### 分段报价如何实现（核心思想）

将同一类机组（如 `coal cc`、`OCGT gas`）拆成多个并联容量块（每块一个 `marginal_cost`），用 **线性规划（LP）** 表达凸分段报价，无需 MILP。

分段参数位于 `config.yaml: dispatch_segmented_prices.carriers.*`：

- `shares`：各段容量占比（和为 1）
- `marginal_cost`：各段边际报价（CNY/MWh 口径由你在配置中标定；代码内部仍按 EUR/MWh 建模，导出再换算）

### 运行阶段 CO2 约束（`co2_limit`）

dispatch-only 阶段会保留网络中的 `GlobalConstraint`（例如 `co2_limit`），即运行阶段仍会受到碳排总量约束。

## 3.3）主要数据来源（电价相关）

电价本身来自 **PyPSA 求解的对偶（LMP）**，不是外部直接输入。影响电价水平的关键外生数据主要来自成本表：

- **成本表**：`data/costs/costs_<year>.csv`（例如 `data/costs/costs_2025.csv`）
  - 每行包含 `source` 与 `further description`，用于追溯公开数据来源与换算假设
  - 典型外生量：煤/气燃料价格 proxy、机组效率、VOM、排放因子等

此外，如果你在二阶段分段报价里显式把“燃料+碳+VOM”打包进 `marginal_cost`（例如 `zero_gas_fuel_marginal_cost: true` 时），则这些分段数值属于**市场标定参数**，应在 README/配置中保留对应的来源口径与年份。

## 3.4）省级可再生装机潜力假设（用于 `p_nom_max`）

为统一省级可再生潜力口径，模型现使用 `data/p_nom/renewable_potential_assumptions_2019.csv` 作为潜力上限假设来源（单位：**万千瓦**）。

- 在 `scripts/prepare_base_network.py` 中，`onwind/offwind/solar` 会读取该表并覆盖 `p_nom_max`（换算关系：`1 万千瓦 = 10 MW`）。
- 其中“水电”列当前主要用于文档记录与后续扩展，现有流程未直接将该列作为可扩建水电上限约束。

表：各省可再生发电装机潜力假设（万千瓦）

| 地区 | 水 | 陆风 | 海风 | 光伏 |
| :-- | --: | --: | --: | --: |
| 全国 | 69440 | 489204 | 298200 | 1580318 |
| 北京 | 0 | 39 | 0 | 1040 |
| 天津 | 0 | 26 | 213 | 557 |
| 河北 | 227 | 23991 | 841 | 6366 |
| 山西 | 563 | 16630 | 0 | 46776 |
| 内蒙古 | 581 | 161016 | 0 | 946787 |
| 辽宁 | 203 | 5099 | 3248 | 4176 |
| 吉林 | 344 | 10669 | 0 | 5924 |
| 黑龙江 | 758 | 18771 | 0 | 9374 |
| 上海 | 0 | 246 | 406 | 466 |
| 江苏 | 174 | 5241 | 2243 | 6858 |
| 浙江 | 614 | 1450 | 3899 | 4621 |
| 安徽 | 312 | 7020 | 0 | 6236 |
| 福建 | 1074 | 3803 | 2501 | 2849 |
| 江西 | 486 | 3661 | 0 | 6601 |
| 山东 | 117 | 18430 | 3603 | 7642 |
| 河南 | 471 | 9116 | 0 | 8636 |
| 湖北 | 1721 | 3798 | 0 | 7704 |
| 湖南 | 1327 | 3558 | 0 | 8803 |
| 广东 | 607 | 9252 | 3863 | 4790 |
| 广西 | 1764 | 12928 | 112 | 8465 |
| 海南 | 84 | 5656 | 800 | 5818 |
| 重庆 | 2296 | 1402 | 0 | 3427 |
| 四川 | 14352 | 14325 | 0 | 10855 |
| 贵州 | 1809 | 6247 | 0 | 6195 |
| 云南 | 10439 | 14168 | 0 | 12465 |
| 西藏 | 20136 | 47609 | 0 | 31773 |
| 陕西 | 1277 | 10340 | 0 | 9446 |
| 甘肃 | 1489 | 17626 | 0 | 13274 |
| 青海 | 2187 | 14227 | 0 | 300834 |
| 宁夏 | 210 | 3900 | 0 | 45639 |
| 新疆 | 3818 | 38960 | 0 | 45921 |

数据来源：

- 《可再生能源数据手册 2019》
- 《建筑领域双碳实施路径研究》[29]
- 山西省光伏数据：[30]
- 内蒙古光伏数据：[31]
- 宁夏光伏数据：[32]（[人民网能源频道](https://paper.people.com.cn/zgnyb/html/2023-03/20/content_25972378.htm)）
- 青海光伏数据：[33]（[青海省人民政府网站](http://www.qinghai.gov.cn/dmqh/system/2021/04/22/010381219.shtml)）

## 4）输出在哪里？

以 `version-<version>` 为例：

- heat-only 求解网络：`results/version-<version>/heatonly_postnetworks/.../heatonly_postnetwork-*.nc`
- 典型日出图：`results/version-<version>/heatonly_plots/.../typical_days/.../*.png`
- 汇总输出（同一目录三份 CSV）：
  - `results/version-<version>/heatonly_summary/.../summary/.../heat-shares.csv`
  - `results/version-<version>/heatonly_summary/.../summary/.../capacities.csv`
  - `results/version-<version>/heatonly_summary/.../summary/.../co2.csv`

## 5）环境提示（重要）

仓库里的 Snakemake 脚本与依赖版本是配套的。如果你使用过新的 PyPSA 版本，可能会遇到类似：

- `ImportError: cannot import name 'Dict' from pypsa.descriptors`

建议优先使用仓库提供的环境文件（见 `envs/` 目录）来创建/激活环境后再运行。
