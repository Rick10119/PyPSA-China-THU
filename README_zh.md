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

