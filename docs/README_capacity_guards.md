# 新能源、核电与电池装机容量限制说明

本文档整理当前模型中额外装机容量限制（capacity guard）的设置、全国目标、分省分摊方法、约束上下限以及数据来源。对应代码位于 `scripts/solar_capacity_guard.py`、`scripts/wind_capacity_guard.py`、`scripts/nuclear_capacity_guard.py` 和 `scripts/storage_capacity_guard.py`；开关和参数位于 `config.yaml`。

配套的完整省级表格见 `data/p_nom/provincial_capacity_guard_limits.csv`。该表给出各资源在各规划年份按省份分摊后的目标、下限、上限，单位为 MW。表中的省级上下限是“扣除已有固定装机、受资源潜力修正之前”的政策目标口径；实际进入模型时会再扣除已有固定装机，并受可开发潜力 `p_nom_max` 限制。

## 1. 约束口径

| 资源 | 启用年份 | 全国目标文件 | 分省依据 | 约束带 | 模型实现要点 |
|---|---:|---|---|---|---|
| 光伏 | 2025-2060 | `data/p_nom/national_solar_capacity_from_external_targets.csv` | `solar_capacity.csv` 中 2010/2015/2020/2025 历史累计装机份额 | 目标的 80%-130% | 分到省后，扣除已有固定光伏；若下限超过省内技术潜力，则放松下限并以上限取潜力值 |
| 陆上风电 | 2025-2060 | `data/p_nom/national_wind_capacity_from_planning.csv` | `onwind_capacity.csv` 中 2010/2015/2020/2025 历史累计装机份额 | 目标的 80%-130% | 无历史份额的省份扩建上限设为 0；其余逻辑同光伏 |
| 海上风电 | 2025-2060 | `data/p_nom/national_wind_capacity_from_planning.csv` | `offwind_capacity.csv` 中 2010/2015/2020/2025 历史累计装机份额 | 目标的 80%-130% | 只在已有海风份额的沿海省份分配；无历史份额省份扩建上限为 0 |
| 核电 | 2030-2060 | `data/p_nom/national_nuclear_capacity_mid_scenario.csv` | `nuclear_capacity.csv` 中 2020/2025 核电装机份额 | 0%-100%，只限上限 | 允许低于目标建设，但禁止超过按历史核电省份份额分到的上限；无历史核电份额省份扩建上限为 0 |
| 电池/新型储能功率 | 2030-2060 | `data/p_nom/national_battery_capacity_from_planning.csv` | `battery_capacity.csv` 中 2025 省级新型储能功率份额 | 0%-100%，只限上限 | 按省分配功率上限，扣除已有固定电池功率；能量容量上限按 `electricity.max_hours.battery = 6` 折算 |

## 2. 全国层面目标与约束带

单位：GW。`下限/上限` 是模型设置的全国等效约束带；对核电和电池，因为下限为 0，表示只设置上限。

| 资源 | 年份 | 全国目标 | 下限 | 上限 | 来源/假设说明 |
|---|---:|---:|---:|---:|---|
| Battery storage power | 2030 | 370.0 | 0.0 | 370.0 | 储能产业研究白皮书2026 / NEA 2030年累计超3.7亿千瓦 |
| Battery storage power | 2035 | 480.0 | 0.0 | 480.0 | 白皮书趋势外推（2030后约30%增量，供参考） |
| Battery storage power | 2040 | 600.0 | 0.0 | 600.0 | 白皮书趋势外推（供参考） |
| Battery storage power | 2045 | 720.0 | 0.0 | 720.0 | 白皮书趋势外推（供参考） |
| Battery storage power | 2050 | 850.0 | 0.0 | 850.0 | 白皮书趋势外推（供参考） |
| Battery storage power | 2055 | 950.0 | 0.0 | 950.0 | 白皮书趋势外推（供参考） |
| Battery storage power | 2060 | 1050.0 | 0.0 | 1050.0 | 白皮书趋势外推（供参考） |
| Nuclear | 2030 | 120.0 | 0.0 | 120.0 | mid_scenario_anchor_from_user |
| Nuclear | 2035 | 160.0 | 0.0 | 160.0 | mid_scenario_anchor_from_user |
| Nuclear | 2040 | 225.0 | 0.0 | 225.0 | mid_scenario_anchor_from_user |
| Nuclear | 2045 | 280.0 | 0.0 | 280.0 | linear_interpolation_2040_2050 |
| Nuclear | 2050 | 335.0 | 0.0 | 335.0 | mid_scenario_anchor_from_user |
| Nuclear | 2055 | 390.0 | 0.0 | 390.0 | designed_extension_for_2060_path |
| Nuclear | 2060 | 450.0 | 0.0 | 450.0 | designed_extension_for_2060_path |
| Offshore wind | 2025 | 47.0 | 37.6 | 61.1 | NEA_2025_renewable_grid_connected_statistics |
| Offshore wind | 2030 | 122.0 | 97.6 | 158.6 | Wind_Beijing_Declaration_2_0_total_1300GW_plus_offshore_15GW_per_year_2026_2030 |
| Offshore wind | 2035 | 200.0 | 160.0 | 260.0 | Declaration_total_2000GW_split_assumption |
| Offshore wind | 2040 | 260.0 | 208.0 | 338.0 | Linear_interpolation_2035_2060 |
| Offshore wind | 2045 | 320.0 | 256.0 | 416.0 | Linear_interpolation_2035_2060 |
| Offshore wind | 2050 | 380.0 | 304.0 | 494.0 | Linear_interpolation_2035_2060 |
| Offshore wind | 2055 | 440.0 | 352.0 | 572.0 | Linear_interpolation_2035_2060 |
| Offshore wind | 2060 | 500.0 | 400.0 | 650.0 | Wind_Beijing_Declaration_2_0_total_5000GW_split_assumption |
| Onshore wind | 2025 | 590.0 | 472.0 | 767.0 | NEA_2025_renewable_grid_connected_statistics |
| Onshore wind | 2030 | 1178.0 | 942.4 | 1531.4 | Wind_Beijing_Declaration_2_0_total_1300GW_plus_offshore_15GW_per_year_2026_2030 |
| Onshore wind | 2035 | 1800.0 | 1440.0 | 2340.0 | Declaration_total_2000GW_split_assumption |
| Onshore wind | 2040 | 2340.0 | 1872.0 | 3042.0 | Linear_interpolation_2035_2060 |
| Onshore wind | 2045 | 2880.0 | 2304.0 | 3744.0 | Linear_interpolation_2035_2060 |
| Onshore wind | 2050 | 3420.0 | 2736.0 | 4446.0 | Linear_interpolation_2035_2060 |
| Onshore wind | 2055 | 3960.0 | 3168.0 | 5148.0 | Linear_interpolation_2035_2060 |
| Onshore wind | 2060 | 4500.0 | 3600.0 | 5850.0 | Wind_Beijing_Declaration_2_0_total_5000GW_split_assumption |
| Solar PV | 2025 | 1200.0 | 960.0 | 1560.0 | NEA_2025_renewable_grid_connected_statistics |
| Solar PV | 2026 | 1380.0 | 1104.0 | 1794.0 | CPIA_2026_additions_180_240GW_general_case_180GW |
| Solar PV | 2030 | 2390.0 | 1912.0 | 3107.0 | CPIA_15th_Five_annual_avg_addition_238_287GW_general_case_238GW |
| Solar PV | 2035 | 3580.0 | 2864.0 | 4654.0 | Post_2030_extension_keep_annual_addition_238GW |
| Solar PV | 2040 | 4770.0 | 3816.0 | 6201.0 | Post_2030_extension_keep_annual_addition_238GW |
| Solar PV | 2045 | 5960.0 | 4768.0 | 7748.0 | Post_2030_extension_keep_annual_addition_238GW |
| Solar PV | 2050 | 7150.0 | 5720.0 | 9295.0 | Post_2030_extension_keep_annual_addition_238GW |
| Solar PV | 2055 | 8340.0 | 6672.0 | 10842.0 | Post_2030_extension_keep_annual_addition_238GW |
| Solar PV | 2060 | 9530.0 | 7624.0 | 12389.0 | Post_2030_extension_keep_annual_addition_238GW |

## 3. 分省方法

分省采用固定历史份额法：先从对应历史装机文件计算省份份额，再把全国目标乘以该份额得到省级目标。公式为：

```text
省级目标容量 = 全国目标容量 × 省份历史装机份额
省份历史装机份额 = 省份在指定历史年份列的装机合计 / 全国在相同历史年份列的装机合计
省级下限 = 省级目标容量 × lower_multiplier
省级上限 = 省级目标容量 × upper_multiplier
```

实际写入 PyPSA 网络时，还会做两步修正：第一，扣除已有不可扩建装机，限制的是新增可扩建容量；第二，如果省内资源潜力低于目标下限，则放松下限，避免模型不可行。

## 4. 省级限制表格怎么读

完整表格在 `data/p_nom/provincial_capacity_guard_limits.csv`，主要字段如下：

| 字段 | 含义 |
|---|---|
| `technology` / `carrier` | 资源类别和模型 carrier |
| `province` | 省份/区域名称，沿用模型输入文件命名 |
| `year` | 规划年份 |
| `share` | 分省份额 |
| `target_mw_before_existing_stock_adjustment` | 按全国目标和份额分到省的目标容量，MW |
| `lower_limit_mw_before_existing_stock_adjustment` | 扣除已有固定装机和资源潜力修正前的省级下限，MW |
| `upper_limit_mw_before_existing_stock_adjustment` | 扣除已有固定装机和资源潜力修正前的省级上限，MW |
| `allocation_basis` | 该省级份额来自哪个历史文件和年份列 |
| `national_source_note` | 全国目标的来源或外推假设 |

## 5. 示例：2030 年省级上限前十

### Solar PV

| 省份 | 份额 | 目标 GW | 下限 GW | 上限 GW |
|---|---:|---:|---:|---:|
| Shandong | 8.15% | 194.89 | 155.91 | 253.36 |
| Jiangsu | 7.66% | 182.96 | 146.37 | 237.85 |
| Hebei | 7.25% | 173.20 | 138.56 | 225.17 |
| Xinjiang | 7.11% | 169.89 | 135.91 | 220.85 |
| Zhejiang | 5.43% | 129.72 | 103.78 | 168.64 |
| Guangdong | 5.24% | 125.12 | 100.10 | 162.66 |
| Anhui | 4.83% | 115.54 | 92.43 | 150.20 |
| Henan | 4.67% | 111.71 | 89.36 | 145.22 |
| Yunnan | 4.62% | 110.49 | 88.40 | 143.64 |
| InnerMongolia | 4.53% | 108.16 | 86.53 | 140.61 |

### Onshore wind

| 省份 | 份额 | 目标 GW | 下限 GW | 上限 GW |
|---|---:|---:|---:|---:|
| InnerMongolia | 13.87% | 163.34 | 130.67 | 212.34 |
| Xinjiang | 8.81% | 103.81 | 83.04 | 134.95 |
| Hebei | 7.07% | 83.26 | 66.61 | 108.23 |
| Gansu | 6.20% | 73.05 | 58.44 | 94.96 |
| Ningxia | 5.19% | 61.13 | 48.91 | 79.48 |
| Henan | 4.75% | 55.90 | 44.72 | 72.67 |
| Shanxi | 4.73% | 55.75 | 44.60 | 72.47 |
| Guangdong | 4.47% | 52.66 | 42.13 | 68.46 |
| Shandong | 3.88% | 45.74 | 36.59 | 59.47 |
| Yunnan | 3.87% | 45.54 | 36.43 | 59.21 |

### Offshore wind

| 省份 | 份额 | 目标 GW | 下限 GW | 上限 GW |
|---|---:|---:|---:|---:|
| Jiangsu | 51.12% | 62.37 | 49.90 | 81.08 |
| Guangdong | 12.97% | 15.83 | 12.66 | 20.57 |
| Shandong | 10.71% | 13.07 | 10.46 | 16.99 |
| Fujian | 10.13% | 12.36 | 9.89 | 16.06 |
| Zhejiang | 5.02% | 6.13 | 4.90 | 7.96 |
| Shanghai | 4.40% | 5.37 | 4.30 | 6.98 |
| Liaoning | 3.09% | 3.77 | 3.01 | 4.90 |
| Hebei | 2.55% | 3.11 | 2.49 | 4.05 |

### Nuclear

| 省份 | 份额 | 目标 GW | 下限 GW | 上限 GW |
|---|---:|---:|---:|---:|
| Guangdong | 29.53% | 35.43 | 0.00 | 35.43 |
| Fujian | 20.19% | 24.23 | 0.00 | 24.23 |
| Zhejiang | 16.63% | 19.95 | 0.00 | 19.95 |
| Jiangsu | 12.09% | 14.51 | 0.00 | 14.51 |
| Liaoning | 10.24% | 12.28 | 0.00 | 12.28 |
| Shandong | 4.97% | 5.97 | 0.00 | 5.97 |
| Guangxi | 3.97% | 4.77 | 0.00 | 4.77 |
| Hainan | 2.38% | 2.85 | 0.00 | 2.85 |
| Gansu | 0.00% | 0.00 | 0.00 | 0.00 |
| Ningxia | 0.00% | 0.00 | 0.00 | 0.00 |

### Battery storage power

| 省份 | 份额 | 目标 GW | 下限 GW | 上限 GW |
|---|---:|---:|---:|---:|
| InnerMongolia | 14.90% | 55.12 | 0.00 | 55.12 |
| Xinjiang | 13.82% | 51.15 | 0.00 | 51.15 |
| Shandong | 8.24% | 30.50 | 0.00 | 30.50 |
| Jiangsu | 7.75% | 28.69 | 0.00 | 28.69 |
| Ningxia | 6.46% | 23.89 | 0.00 | 23.89 |
| Guangdong | 5.38% | 19.91 | 0.00 | 19.91 |
| Hebei | 4.90% | 18.11 | 0.00 | 18.11 |
| Gansu | 4.66% | 17.25 | 0.00 | 17.25 |
| Hunan | 3.91% | 14.47 | 0.00 | 14.47 |
| Zhejiang | 3.81% | 14.10 | 0.00 | 14.10 |

## 6. 数据来源备忘

- 光伏全国目标：2025 年采用国家能源局可再生能源并网统计口径；2026 和 2030 年基于 CPIA 2026 预测及“十五五”新增装机假设；2030 年后按年增量延展。
- 风电全国目标：2025 年采用国家能源局统计；2030/2035/2060 年参考“风能北京宣言 2.0”总量路径及海风拆分假设，中间年份线性插值。
- 核电全国目标：采用当前配置中的中情景锚点和 2040-2050 线性插值、2060 路径延展。
- 电池/新型储能全国目标：2025 年采用国家能源局新型储能累计投运规模口径；2027/2030 年采用专项行动方案和储能产业研究白皮书相关目标；2030 年后为趋势外推。
- 省级份额：均来自 `data/existing_infrastructure/` 下对应资源的历史省级装机表。
