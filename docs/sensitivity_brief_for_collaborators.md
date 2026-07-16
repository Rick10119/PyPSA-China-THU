# 光伏价值敏感性分析简要说明（合作者版）

本文说明交付的 `solar_value_dataset_*.xlsx` 含义、三类敏感性情景，以及 core 情景采用的关键成本与装机上限假设。合作者收到的文件即下文「交付文件」结构；无需依赖仓库内其他结果目录。

## 1. 交付文件

```text
collected_solar_value_files/
├── storage/
│   ├── solar_value_dataset_storage_x0p7.xlsx
│   ├── solar_value_dataset_storage_x1.xlsx
│   ├── solar_value_dataset_storage_x1p5.xlsx
│   └── solar_value_dataset_storage_x2.xlsx
├── thermal_flexibility/
│   ├── solar_value_dataset_threshold_0.xlsx
│   ├── solar_value_dataset_threshold_0p1.xlsx
│   ├── solar_value_dataset_threshold_0p2.xlsx
│   ├── solar_value_dataset_threshold_0p3.xlsx
│   ├── solar_value_dataset_threshold_0p4.xlsx
│   ├── solar_value_dataset_threshold_0_lmp.xlsx
│   └── solar_value_dataset_threshold_0_sync.xlsx
└── wind/
    ├── solar_value_dataset_wind_cheap_x0p8.xlsx
    ├── solar_value_dataset_wind_cheap_x0p6.xlsx
    └── solar_value_dataset_wind_cheap_x0p4.xlsx
```

每个 xlsx 为省级–年度表。主要字段：

| 字段 | 含义 |
|---|---|
| `solar_ele_GWh` | 该省该年光伏发电量 |
| `value_factor_numerator` | 光伏发电量加权平均电价（PV weighted price） |
| `value_factor_denominator` | 系统发电量加权平均电价（system weighted price） |
| `value_factor` | 光伏价值因子 = numerator / denominator |
| `solar_penetration` | 光伏渗透率 |
| `solar_curtailment_rate` | 弃光率 |
| `solar_capacity_factor` | 光伏容量因子 |

全国曲线可用 `solar_ele_GWh` 对各省 `value_factor` 做发电量加权平均。

**Core 对照文件**：`storage/solar_value_dataset_storage_x1.xlsx`（储能上限 ×1，价格口径等同 `threshold_0p4`）。

## 2. Core 情景与三类敏感性

### 2.1 Core

- 储能全国累计上限倍率：`1.0`（`storage_x1`）
- 价格口径：火电/同步机低出力阈值 `0.4`（`threshold_0p4`）
- 光伏、风电、电池资本成本倍数：均为 `1.0`
- 风光全国装机目标带：目标的 `80%–130%`
- 同步机出力底线（相对本省 AC 电力负荷）：2025–2050 为 `10%`，2055 为 `5%`，2060 为 `1%`

### 2.2 储能容量敏感性（`storage/`）

完整容量扩张重跑；只改储能全国累计**严格上限**倍率，电池成本仍为 `1.0x`，价格口径同 `threshold_0p4`。

| 文件后缀 | 储能上限相对 core 目标 |
|---|---:|
| `storage_x0p7` | 70% |
| `storage_x1` | 100%（core） |
| `storage_x1p5` | 150% |
| `storage_x2` | 200% |

预期方向：储能上限越高，弃光通常越低，光伏 value factor 通常越高。

### 2.3 火电灵活性敏感性（`thermal_flexibility/`）

不重跑容量扩张；在 `storage_x1` 已求解网络上，只改价格后处理的低出力置零阈值。

| 文件后缀 | 含义 |
|---|---|
| `threshold_0` | 阈值 0，不含同步机底线 mask（主曲线端点） |
| `threshold_0p1` … `threshold_0p4` | 阈值 0.1–0.4；`threshold_0p4` = core 价格口径 |
| `threshold_0_lmp` | 纯 planning LMP 参考线 |
| `threshold_0_sync` | 阈值 0 + 仅同步机底线 mask（参考线） |

置零规则（简化）：

```text
若本省同步机组参考出力 < 当日最大同步机组参考出力 × threshold
→ 该省该小时价格置 0（仅在有光伏出力的小时生效）
```

同步机参考集合含煤电、核电、气电、CHP、生物质及 AC 侧水电；不含抽蓄与 `hydro_inflow`。主曲线一般满足 `VF(0) ≥ VF(0.1) ≥ … ≥ VF(0.4)`；两条参考线不参与该序列。

### 2.4 风电降本敏感性（`wind/`）

完整容量扩张重跑；降低风电资本成本，并同步放宽风电装机上限。光伏与电池成本保持 core；价格口径同 `threshold_0p4`。

| 文件后缀 | 风电成本倍数 | 风电装机上限（相对全国目标） |
|---|---:|---:|
| `wind_cheap_x0p8` | 0.8× | 150%（core 为 130%） |
| `wind_cheap_x0p6` | 0.6× | 200% |
| `wind_cheap_x0p4` | 0.4× | 250% |

预期方向：风电更便宜后装机可能上升并挤出部分光伏，从而改变光伏 value factor。


---

## 3. 关键成本假设（core = 1.0×）

贴现率 **7%**；光伏/陆风/海风寿命 **25 年**。对照换算可用 **7.8 CNY/EUR**。

### 3.1 投资成本轨迹（EUR）

| 年份 | 光伏 EUR/kW | 陆风 EUR/kW | 海风 EUR/kW | 电池功率 EUR/kW | 电池能量 EUR/kWh | 4h 电池折合 EUR/kWh |
|---:|---:|---:|---:|---:|---:|---:|
| 2025 | 366 | 487 | 1282 | 60.3 | 99.2 | ~114 |
| 2030 | 300 | 418 | 1174 | 44.9 | 75.3 | ~87 |
| 2040 | 252 | 375 | 897 | 30.5 | 51.2 | ~59 |
| 2050 | 240 | 331 | 841 | 16.8 | 39.8 | ~44 |
| 2055 | 228 | 310 | 827 | 16.8 | 39.8 | ~44 |
| 2060 | 216 | 288 | 814 | 16.8 | 39.8 | ~44 |

说明：4h 折合 ≈ `(battery inverter + 4 × battery storage) / 4`。2050 后电池投资不再下降；光伏与风电继续缓降。

约合 CNY（×7.8）：

| 年份 | 光伏 CNY/kW | 陆风 CNY/kW | 海风 CNY/kW | 4h 电池 CNY/kWh |
|---:|---:|---:|---:|---:|
| 2025 | ~2,855 | ~3,800 | ~10,000 | ~891 |
| 2030 | ~2,340 | ~3,263 | ~9,157 | ~675 |
| 2050 | ~1,872 | ~2,584 | ~6,562 | ~343 |
| 2060 | ~1,685 | ~2,246 | ~6,346 | ~343 |

### 3.2 成本来源

| 技术 | 来源摘要 |
|---|---|
| 光伏 | Sun et al.《碳达峰碳中和目标下电力系统成本与价格水平预测》基线 + 2020–2060 降本路径 |
| 陆风 | 2020 基线：中国电力行业年度发展报告 / CCTD；远期趋势：NREL ATB |
| 海风 | 2020–2030：Danish Energy Agency；2030–2060：Sun et al. 路径（不含并网附加项） |
| 电池 | UCSB China power system template（约 2700 CNY/kW·4h）+ NEA 新型储能相关报告；寿命/效率等部分参数来自 DEA storage catalogue |

风电降本情景：上表陆风/海风投资 × `0.8 / 0.6 / 0.4`；光伏与电池按照基准情景（逐年下降）。

---

## 4. 关键装机上限假设

### 4.1 全国目标轨迹（未乘 guard 倍率前）

**光伏**（CPIA 2026 预测与后续年均新增约 238 GW 外推；2025 为 NEA 并网统计）

| 年份 | 目标 GW |
|---:|---:|
| 2025 | 1,200 |
| 2030 | 2,390 |
| 2050 | 7,150 |
| 2060 | 9,530 |

**陆风 / 海风**（2025：NEA 统计；2030+：北京风能宣言 2.0 总量目标及分拆/插值假设）

| 年份 | 陆风目标 GW | 海风目标 GW | 风电合计 GW |
|---:|---:|---:|---:|
| 2025 | 590 | 47 | 637 |
| 2030 | 1,178 | 122 | 1,300 |
| 2050 | 3,420 | 380 | 3,800 |
| 2060 | 4,500 | 500 | 5,000 |

**新型储能（电池）**（NEA /《新型储能规模化建设专项行动方案》/《储能产业研究白皮书 2026》轨迹；全国总量约束，无省级分摊）

| 年份 | 累计目标 GW | 备注 |
|---:|---:|---|
| 2025 | 136 | 已投运统计（guard 自 2030 年起生效） |
| 2030 | 480 | 另设政策下限 370 GW；模型上限取 480 GW |
| 2050 | 850 | 白皮书趋势外推 |
| 2060 | 1,000 | 白皮书趋势外推 |

### 4.2 Guard 如何落到优化约束

| 技术 | 目标带（相对上表全国目标） | Core 实际可建区间 | 敏感性如何改 |
|---|---|---|---|
| 光伏 | 下限 80%、上限 130% | 目标 × `[0.8, 1.3]` | 本批敏感性不改 |
| 风电（陆+海分别） | 下限 80%、上限 130% | 目标 × `[0.8, 1.3]` | 降本情景把**上限**改为 1.5 / 2.0 / 2.5；下限仍 0.8 |
| 电池储能 | 下限 0%、上限 100% | 允许少建，**禁止超过**目标 | `storage_x*` 把目标整体 × 0.7 / 1.0 / 1.5 / 2.0 后再取上限 |

因此 core 下例如：

- 2050 光伏上限 ≈ 7,150 × 1.3 ≈ **9,295 GW**
- 2050 陆风上限 ≈ 3,420 × 1.3 ≈ **4,446 GW**；海风上限 ≈ 380 × 1.3 ≈ **494 GW**
- 2050 电池上限 = 850 × 1.0 = **850 GW**（`storage_x2` 则为 1,700 GW）

省内风光份额按历史累计装机比例分摊；储能仅为全国总量约束。
