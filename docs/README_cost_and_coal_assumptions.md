# 煤电运行约束与光伏/储能成本假设说明

本文档整理当前 PyPSA-China-THU 模型中与**煤电最小出力/爬坡**、**光伏与电池投资成本**相关的 input 现状，并与公开预测及中国市场现价对比。目的是为下一步修改 `data/costs/costs_*.csv` 或相关 config 提供依据。

**相关文件**

| 类型 | 路径 |
|------|------|
| 年度成本表 | `data/costs/costs_{2025,2030,...,2060}.csv` |
| 成本加载逻辑 | `scripts/add_electricity.py` → `load_costs()` |
| 网络组件定义 | `scripts/prepare_base_network.py` |
| 存量机组 | `scripts/add_existing_baseyear.py` |
| 全局 config | `config.yaml` |
| 光伏成本路径来源说明 | Sun et al. 2023（见下文参考文献） |
| 储能 2025 基准注释 | UCSB China template + NEA 新型储能报告 |

**通用换算约定（文档内对照用）**

- 汇率：**7.8 CNY/EUR**（与 `config.yaml` / 成本表注释一致）
- 储能 4h 系统总造价（功率口径）：`battery inverter + 4 × battery storage` [EUR/kW]
- 储能能量口径：**系统 EUR/kW ÷ 时长 ≈ EUR/kWh**，再 × 7.8 得 CNY/kWh
- 模型中电池时长上限：`config.yaml` → `electricity.max_hours.battery: 6`

---

## 1. 煤电：最小出力与爬坡

### 1.1 当前模型设定

| 参数 | 煤电纯凝 (`coal power plant`) | 煤电 CHP (`CHP coal`) | 备注 |
|------|--------------------------------|----------------------|------|
| `p_min_pu` | **未设置**（PyPSA 默认 **0**） | **未设置**（默认 0） | 见 `prepare_base_network.py` 1067–1134 行 |
| `ramp_limit_up/down` | **全库无此参数** | 同左 | 小时级 LP 可自由调节 |
| 核电对比 | — | — | 核电 `p_min_pu = 0.7`（`prepare_base_network.py` 909 行） |

煤电在优化中**理论上可关至 0**，没有显式爬坡约束。

### 1.2 等效“必须运行”约束（非 p_min_pu）

规划阶段在 `config.yaml` → `synchronous_generation_floor`：

```yaml
synchronous_generation_floor:
  enabled: true
  ratio: 0.10   # 本地同步电源出力 ≥ 10% × 本地 AC 负荷
  Generator: [coal power plant, coal cc, nuclear]
  Link: [OCGT gas, CHP gas, CHP coal, biomass]  # 仅 AC 出力侧
```

实现：`scripts/solve_network_myopic.py` → `add_synchronous_generation_floor_constraints()`。

二阶段 dispatch 价格重构（`dispatch_segmented_prices`）中：

- 煤电分 5 段报价，最低段占装机 **40%**
- `thermal_load_floor.ratio: 0.10`：低出力时段可映射为零价

### 1.3 若下一步要改煤电灵活性

| 目标 | 建议改动位置 |
|------|-------------|
| 加最小出力 | `prepare_base_network.py` 煤电 `Generator`/`Link` 增加 `p_min_pu`；存量机组在 `add_existing_baseyear.py` |
| 加爬坡 | 同上，增加 `ramp_limit_up` / `ramp_limit_down`（当前代码库从未使用） |
| 调同步电源下限 | `config.yaml` → `synchronous_generation_floor.ratio` |
| 调 dispatch 报价形状 | `config.yaml` → `dispatch_segmented_prices.carriers` |

---

## 2. 模型内成本 input（2025 vs 2050）

数据来自 `data/costs/costs_2025.csv` 与 `costs_2050.csv` 的 `investment` 字段（原始单位 EUR/kW 或 EUR/kWh，经 `load_costs` 转为 `capital_cost`）。

### 2.1 光伏（`solar`）

| 年份 | 投资 [EUR/kW] | 约合 [CNY/kW] | 边际成本 [EUR/MWh] |
|------|---------------|---------------|-------------------|
| **2025** | 366 | ~2,855 | 0.01 |
| **2030** | 300 | ~2,340 | 0.01 |
| **2050** | 240 | ~1,872 | 0.01 |
| **2060** | 216 | ~1,685 | 0.01 |

- 成本路径：Sun et al. 2023 + 线性外推至 2060（见 `costs_*.csv` 中 `solar` 的 source 列）
- `load_costs()` 中 `solar` 资本成本 = (`solar-rooftop` + `solar-utility`) / 2
- 贴现率：**7%**（`config.yaml` → `costs.discountrate`），寿命 **25 年**

### 2.2 电池（`battery inverter` + `battery storage`）

| 年份 | 功率 [EUR/kW] | 能量 [EUR/kWh] | 4h 系统 [EUR/kW] | 4h 约合 [CNY/kWh] | 6h 约合 [CNY/kWh] |
|------|---------------|----------------|------------------|-------------------|-------------------|
| **2025** | 60.3 | 99.2 | 457 | **~891** | ~852 |
| **2030** | 44.9 | 75.3 | 346 | ~675 | ~646 |
| **2050** | 16.8 | 39.8 | 176 | **~343** | ~332 |
| **2055–2060** | 16.8 | 39.8 | 176 | ~343 | ~332 |

注意：**2050 之后电池投资在 CSV 中不再下降**（2050/2055/2060 相同），仅光伏继续缓降。

模型结构（`prepare_base_network.py`）：

- `Store`：能量侧，`capital_cost` ← `battery storage`
- `Link` 充/放各承担 **50%** 逆变器 `capital_cost`
- 往返效率：**96%**（单链路 √0.96）
- 存量电池平均时长：**2.58 h**（`config.yaml` → `existing_capacities.battery_max_hours_existing`）

### 2.3 全轨迹速查（4h 储能 + 光伏）

| 年份 | 光伏 CNY/kW | 储能 4h CNY/kWh |
|------|-------------|-----------------|
| 2025 | 2,855 | 891 |
| 2030 | 2,340 | 675 |
| 2035 | 2,153 | 559 |
| 2040 | 1,966 | 444 |
| 2045 | 1,919 | 393 |
| 2050 | 1,872 | **343** |
| 2055 | 1,778 | 343 |
| 2060 | 1,685 | 343 |

---

## 3. 外部对照：2050 是否偏低？

### 3.1 储能——**很可能偏低（主要风险）**

#### 中国市场现价（2025，已低于模型 2025 假设）

| 来源 | 口径 | 价格 |
|------|------|------|
| [寻熵研究院 2025](https://www.seeconsulting.cn/2025%e5%b9%b4%e5%82%a8%e8%83%bd%e7%b3%bb%e7%bb%9f%e5%92%8cepc%e4%bb%b7%e6%a0%bc%e5%85%a8%e6%99%af%e5%88%86%e6%9e%90/) | 4h **系统**（不含 EPC） | **~0.45 元/Wh（450 元/kWh）** |
| 同来源 | 4h EPC | ~0.90 元/Wh |
| CNESA / 能源界 2024 | 2h 系统中标均价 | ~628 元/kWh |

模型 **2050** 假设 **343 元/kWh**，比 **2025 年国内 4h 系统现价还低约 24%**。若认为 2024–2025 价格已接近底部，则 2050 不应再显著低于现价。

模型 **2025** 假设 **891 元/kWh**，反而**高于**当前市场系统价（450 元/kWh）——说明 CSV 中 2025 基准可能偏 stale，但 2025→2050 的学习曲线降得太陡，最终 2050 仍落在现价以下。

#### Sun et al. 2023（模型光伏路径同一文献）

[《碳达峰、碳中和目标下的电力系统成本及价格水平预测》](https://www.electricpower.com.cn/CN/10.11930/j.issn.1004-9649.202208069)，图 10 电化学储能（元/kW，功率口径）：

| 年份 | 3h [CNY/kW] | 6h [CNY/kW] | 6h 折合 [CNY/kWh] |
|------|-------------|-------------|-------------------|
| 2025 | ~4,200 | ~7,400 | ~1,230 |
| 2030 | ~3,100 | ~5,600 | ~930 |
| **2050** | **~2,560** | **~4,550** | **~760** |
| 2060 | ~2,360 | ~4,130 | ~690 |

模型 2050（6h）：**~332 CNY/kWh** vs Sun et al. **~760 CNY/kWh** → 约低 **56%**。

文献还给出 2060 阶段锂/钠电池 **~500 元/kWh** 的目标；模型 2050 已低于该 2060 下限。

#### NREL 2025 储能成本更新（4h 完整系统）

[NREL/OSTI Cost Projections 2025 Update](https://www.osti.gov/servlets/purl/2583471)，2050（2024 USD/kWh）：

| 情景 | USD/kWh | 约合 CNY/kWh (×7.2) |
|------|---------|---------------------|
| Low | 108 | ~780 |
| **Mid** | **178** | **~1,280** |
| High | 307 | ~2,210 |

模型 2050：**~343 CNY/kWh** ≈ NREL Mid 的 **27%**，仅接近钠离子 + 高学习率极端情景（文献 ~225–405 CNY/kWh）。

#### 国内新型电力系统路线图

[《中国新型电力系统发展蓝皮书》相关研究](https://www.electricpower.com.cn/CN/article/downloadArticleFile.do?attachType=PDF&id=4043) 锂/钠电池成本阶段目标：

| 阶段 | 目标 [CNY/kWh] |
|------|----------------|
| 2030 前 | 800–1,000 |
| 中期 | 500–700 |
| 2060 | ~500 |

### 3.2 光伏——**略偏低，可接受**

| 来源 | 2050 左右 [CNY/kW] | vs 模型 1,872 |
|------|---------------------|---------------|
| **Sun et al.**（3500→1900，2020–2060 线性外推） | **~2,300** | 模型低 ~19% |
| **水电总院 CREEI 2024** 大型地面光伏实际 | **~3,450** | 模型低 ~46% |
| **DNV ETO 2023** 全球 | $560/kW ≈ **~4,000+** | 模型低 ~50%+ |
| Sun et al. **2060** 终点 | 1,900 | 模型 2050 已接近其 2060 |

在中国本土语境下，2050 光伏 **~1,870 CNY/kW** 有“持续降本”叙事支撑，但相对 Sun et al. 自身曲线仍偏乐观 **15–25%**。

### 3.3 总判断

| 技术 | 2050 是否太低？ | 程度 | 对优化的影响 |
|------|----------------|------|-------------|
| **储能** | **是** | 相对 Sun/NREL/国内路线图均显著偏低；且低于 2025 市场系统价 | 储能可能被**过度配置** |
| **光伏** | **略低** | 相对 Sun et al. ~低 20%；相对 2024 实际 ~低 45% | 光伏可能略偏多，但不如储能敏感 |

---

## 4. 建议的 sensitivity 情景（供改代码）

在不动其他参数的前提下，可先复制 `costs_2050.csv`（及必要时 2030/2040/2045）做情景分支。

### 4.1 推荐三档（2050，`solar` + `battery storage` + `battery inverter`）

| 情景 | `solar` [EUR/kW] | 4h 系统目标 [CNY/kWh] | 实现方式（示例） |
|------|------------------|----------------------|------------------|
| **Low（当前模型）** | 240 | ~343 | 现有 `costs_2050.csv` |
| **Mid（推荐参考）** | **280–300** | **~500–620** | 对齐 Sun 2060 / 国内路线图中期 |
| **High（保守）** | 320–340 | ~780–900 | 接近 NREL Low 或 2025 现价持平 |

**Mid 情景 2050 示例数值**（4h 系统 ≈ 500 CNY/kWh）：

```text
battery inverter  investment ≈ 32 EUR/kW   (~250 CNY/kW)
battery storage   investment ≈ 58 EUR/kWh  (~450 CNY/kWh)
# 32 + 4×58 = 264 EUR/kW → 264/4×7.8 ≈ 515 CNY/kWh

solar             investment ≈ 295 EUR/kW   (~2,300 CNY/kW，贴近 Sun 2050 外推)
```

**High 情景 2050 示例**（4h 系统 ≈ 450 CNY/kWh，与 2025 市场系统价持平、不再降）：

```text
battery inverter  investment ≈ 45 EUR/kW
battery storage   investment ≈ 52 EUR/kWh
# 45 + 4×52 = 253 EUR/kW → ~494 CNY/kWh

solar             investment ≈ 320 EUR/kW
```

### 4.2 2025 基准是否需要同步下调

当前模型 2025 储能 **~891 CNY/kWh**，高于 2025 市场 4h 系统 **~450 CNY/kWh**。若希望全时段与市场一致，可考虑：

1. 将 `costs_2025.csv` 电池投资下调至接近 **450 CNY/kWh**（4h）
2. 2050–2060 不再低于 **400–500 CNY/kWh**（除非明确采用钠离子 breakthrough 情景）
3. 在 `costs_2030.csv` … `costs_2045.csv` 做平滑插值，避免 2025 高、2050 突然跳低

### 4.3 代码改动 checklist

- [ ] 编辑 `data/costs/costs_{year}.csv` 中 `solar`、`battery inverter`、`battery storage` 的 `investment`
- [ ] 确认 `load_costs()` 会按规划年读取对应 CSV（`prepare_base_network.py` / snakemake 规则中的 `cost_year`）
- [ ] 若做情景分支：在 snakemake config 或 `configs/` 中增加 `costs_file` / `cost_scenario` 开关
- [ ] 重跑后对比：`summary/.../capacities.csv` 中 battery vs solar 装机变化
- [ ] 可选：在 `config.yaml` → `aluminum.scenario_dimensions.market_opportunity` 已有 `battery_cost_factor`（0.8/1.0/1.5），可复用于铝相关情景，**但不作用于主网络电池**

---

## 5. 煤电成本（顺带参考，非本次重点）

| 参数 | 2025/2050（`coal` in costs CSV） |
|------|----------------------------------|
| 投资 | 505 EUR/kW_e |
| 燃料 | 13.93 EUR/MWh_th（2025 中国 5500 大卡 proxy，未来年同比例缩放） |
| 效率 | ~0.45（2025）→ ~0.50（2050），线性外推 |
| 寿命 | 40 年 |

---

## 6. 参考文献与链接

| 编号 | 文献 / 来源 | 用途 |
|------|------------|------|
| [1] | Sun Q. et al., 《碳达峰、碳中和目标下的电力系统成本及价格水平预测》, 中国电力 2023 | 模型 PV/储能成本路径；图 7 光伏、图 10 储能 |
| [2] | NREL, *Cost Projections for Utility-Scale Battery Storage: 2025 Update* | 2050 4h 系统 $108–307/kWh |
| [3] | 寻熵研究院, 《2025年储能系统和EPC价格全景分析》 | 2025 国内 4h 系统 ~0.45 元/Wh |
| [4] | CREEI / 水电总院, 2024 可再生能源造价报告（via Medium 转载） | 2024 大型光伏 ~3,450 元/kW |
| [5] | DNV, *Energy Transition Outlook 2023* | 全球 2050 PV ~$560/kW |
| [6] | Keiner et al., *Sodium-ion battery cost projections…2050*, Applied Energy 2025 | 极端乐观储能 ~225–405 CNY/kWh |
| [7] | 《中国新型电力系统发展蓝皮书》相关研究, 中国电力 | 储能 800→500 元/kWh 分阶段目标 |
| [8] | UCSB China power system template + NEA 新型储能 | `costs_2025.csv` 电池注释来源 |

---

## 7. 文档维护

- **创建**：2026-06-17，基于 `config.yaml` 与 `data/costs/costs_*.csv` 审查及外部预测对照
- **下一步**：按 §4 修改成本 CSV 或增加 `cost_scenario` 分支后，在本节记录实际采用的数值与一次对比跑结果（装机、LCOS 变化）

<!-- 改代码后在此追加：
## 8. 变更记录
- YYYY-MM-DD: 采用 Mid 情景，更新 costs_2050.csv；2050 储能装机从 X GW 变为 Y GW
-->
