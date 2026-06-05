# 灵活性资源跨时间尺度成本计算方法说明

本文档说明 `scripts/plot_flexibility_cost_timescales.py` 当前采用的计算方法，并与旧方案 `docs/储能与用户侧灵活性顶峰成本分析.md` 做口径对比。原则是：旧方案没有覆盖、但当前图需要的内容予以保留；旧方案与当前计算不一致的地方，以当前脚本口径为准。

## 1. 与旧方案的主要差异

| 模块 | 旧方案口径 | 当前口径 | 处理 |
| --- | --- | --- | --- |
| 系统价值 | 主要使用峰谷价差、顶峰价值等概念性表达 | 拆分为 2025 与 2050 两条线，均使用 marginal price，并参考 `evaluate_storage_cycles.py` 的 Top-H 价值算法 | 采用当前口径 |
| 时间尺度 | 用调用时长 `T` 和年调用次数 `n` 表达 | 固定为 intraday、daily、weekly、monthly、seasonal/year 五个点，并统一 duration 与 cycles | 采用当前口径 |
| 储能成本 | LCPE/LCOS 框架，区分功率成本、能量成本、效率损失、充电电价 | 用本地 `data/costs/costs_2025.csv` 的电池与逆变器成本，按时间尺度逐项年化摊销，并计入效率损失 | 采用当前口径 |
| 氢储能 | 旧方案未单独展开 power-to-hydrogen-to-power 链条 | 显式计算 electrolyser、fuel cell、underground hydrogen storage、转换效率损失 | 新增并保留 |
| 用户侧无过剩产能 | 旧方案的“产能超配模式”按超配比例和利用率推导 | 当前图按新增可用负荷能力的资本成本计算，折算为 CNY/kW load，再按年调用次数分摊，并按 `docs/季节性生产资金成本计算.md` 的 WCR 面积法计算库存/营运资金占用；铝和钢另计物理仓储费 | 采用当前口径 |
| 用户侧牺牲产量 | 旧方案有“直接影响生产 / 机会成本”场景 | 当前图不画该场景，避免把应急停产的高机会成本与可规划灵活性混在一起 | 保留为未计入说明 |
| 用户侧有过剩产能 | 旧方案认为资本成本和机会成本近似为零，主要剩直接运行成本 | 当前图把既有过剩产能的资本视为沉没，但保留小额固定代理成本、按 WCR 面积法计算的营运资金占用、铝/钢的物理仓储费和随时长增加的调度延迟代理成本 | 新增并保留 |
| 数据中心 | 旧方案未覆盖 | 当前图区分 no excess capacity 与 sunk excess capacity | 新增并保留 |

## 2. 统一时间尺度

所有曲线统一折算为 `CNY per kWh shifted or made available`。当前脚本使用以下时间尺度：

| time scale | duration_h | cycles_per_year | 含义 |
| --- | ---: | ---: | --- |
| intraday_hours | 6 | 365 | 日内 6 小时转移，每天一次 |
| daily | 24 | 182.5 | 24 小时转移，约每两天一次 |
| weekly | 168 | 52 | 7 天转移，每周一次 |
| monthly | 720 | 12 | 30 天转移，每月一次 |
| seasonal_year | 2160 | 1 | 约 3 个月转移，每年一次 |

## 3. System value：marginal price Top-H 方法

系统价值不再使用 mapped price 或简单峰谷价差，而是使用 marginal price，并参考 `evaluate_storage_cycles.py` 的思路计算：在每个时间窗口内，找出最有价值的 Top-H 小时 marginal price，作为“把 1 kWh 用电从该时间尺度的高价值时段移走”的单位系统价值。

当前使用两组本地结果：

| 曲线 | 数据源 | 单位处理 |
| --- | --- | --- |
| system value, 2025 | `results/version-0602.1H.2/prices/dispatch_segmented/positive/dispatch_segmented_prices-ll-current+FCG-linear2050-2025_nodal_marginal.csv` | 文件为 CNY/MWh，除以 1000 得到 CNY/kWh；当前文件只包含 Shandong |
| system value, 2050 CN | `results/version-0120.1H.1-MMMF-2050-15p/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2050.nc` | 读取 31 个 AC 省级 bus 的 `buses_t.marginal_price`，按 7.8 CNY/EUR 从 EUR/MWh 转为 CNY/kWh |

Top-H 计算步骤：

1. 对每个 bus 或价格序列，按时间尺度切成窗口 `W`。
2. 在每个窗口内将 marginal price 从高到低排序。
3. 取前 `H` 小时的加权平均。如果 `H` 不是整数，最后一小时按小数权重计入。
4. 对所有窗口求平均；2050 还会进一步对所有 AC bus 求平均。

当前 `H` 和窗口 `W` 的设置为：

| time scale | window W | Top-H H |
| --- | ---: | ---: |
| intraday_hours | 6 h | 2.6 h |
| daily | 24 h | 2.6 h |
| weekly | 168 h | 24 h |
| monthly | 730 h | 168 h |
| seasonal_year | 8760 h | 168 h |

这个值表示系统愿意为“避开该窗口内最高 marginal price 时段的 1 kWh 用电”支付的平均价值。它不是储能套利净收益，因此没有扣除低价充电成本。

## 4. Battery storage shifting cost

电池成本来自本地 `data/costs/costs_2025.csv`：

- `battery storage`: energy capacity investment 与 lifetime
- `battery inverter`: power capacity investment、lifetime、FOM、efficiency

对每个时间尺度，单位搬运成本为：

```text
battery_cost =
  [energy_capex * CRF(energy_lifetime)
   + (power_capex / duration_h) * (CRF(power_lifetime) + power_fom)]
  / cycles_per_year
  + charge_electricity_price * (1 / round_trip_efficiency - 1)
```

其中 `charge_electricity_price = 0.30 CNY/kWh`，用于估算效率损失对应的补电成本。该口径沿用了旧方案中“资本年化 + 充电/效率损失”的思想，但当前图采用专属资产按年循环次数分摊，没有使用旧文档中“时间占比分摊”的共享资产口径。

## 5. Hydrogen storage shifting cost

氢储能按 power-to-hydrogen-to-power 链条计算，使用本地成本表中的：

- `electrolysis`
- `fuel cell`
- `hydrogen storage underground`

对输出端 1 kWh 电量，容量需求为：

```text
electrolyser_kw_per_kwh_out = 1 / (eta_electrolyser * eta_fuel_cell * duration_h)
fuel_cell_kw_per_kwh_out    = 1 / duration_h
h2_kwh_per_kwh_out          = 1 / eta_fuel_cell
```

单位搬运成本为上述三类资产年化成本按 `cycles_per_year` 分摊，再加转换损失：

```text
hydrogen_cost =
  annualized_pt_h2_p_assets / cycles_per_year
  + charge_electricity_price * (1 / (eta_electrolyser * eta_fuel_cell) - 1)
```

这是旧方案没有单独覆盖的内容，当前图保留。

## 6. 用户侧：no excess capacity

`no_excess_capacity` 表示没有可直接调用的闲置产能或闲置算力。若要提供灵活性，需要新增可用负荷能力，因此成本主要来自新增能力的资本年化摊销。

对铝和钢，先把产能资本开支从 `CNY/(t/year)` 转为 `CNY/kW load`：

```text
capex_cny_per_kw_load =
  capex_cny_per_annual_tonne / (electricity_kwh_per_tonne / 8760)
```

然后按年调用次数分摊：

```text
capacity_cost =
  capex_cny_per_kw_load * CRF(lifetime_years) / cycles_per_year
```

对铝和钢，另加库存/营运资金占用。该项按 `docs/季节性生产资金成本计算.md` 的逻辑处理：不是简单取年度平均余额，而是比较“集中/周期性生产、均衡销售”与“均衡生产、均衡销售”两种情景下的增量 WCR 面积。落到当前五个离散时间尺度时，资金被锁住的年化时间取：

```text
working_capital_lock_years =
  max(duration_h / 8760, 0.5 / cycles_per_year)
```

其中 `duration_h / 8760` 表示一批产品至少被推迟销售或提前生产的直接占用时间；`0.5 / cycles_per_year` 表示周期性生产-销售错配形成的三角形增量库存余额面积。seasonal/year 因为一年一次调用，三角 WCR 面积对应约半年平均资金占用，而不是只按 3 个月计算。

因此：

```text
inventory_cost =
  product_value_cny_per_tonne
  * discount_rate
  * working_capital_lock_years
  / electricity_kwh_per_tonne
```

最终：

```text
no_excess_cost = capacity_cost + inventory_cost + physical_warehousing_cost
```

物理仓储费与资金占用分开计算。参考“仓储成本计算说明（供迁移）”：季节性运行下，工厂需在采购、生产、销售之间解耦，因此原料或成品都可能产生库存。物理仓储成本可由库存量、密度、有效堆高、储存效率和仓租推导：

```text
Inv(t) = cumsum(Production) - cumsum(Demand)
Area = Inv / (density * height * efficiency)
Physical storage cost = Area * rental_rate * time
```

当前图没有逐时重建原料和成品库存面积，而采用按时间尺度折算的保守上界。铝采用成品铝+氧化铝叠加口径：

```text
aluminium_warehousing_cost =
  6.9 CNY/(t-Al month)
  * min(duration_h / 720, 2 months)
  / 13,300 kWh/t-Al
```

其中 `6.9 CNY/(t month)` 对应约 `1 USD/t/month`；seasonal/year 封顶为约 2 个月，得到约 `14 CNY/t-Al`，即 `0.00105 CNY/kWh`。该项只代表物理仓储费，已经不包含运输成本，也不包含 WACC/库存资金占用。

钢采用同一方法，但因钢材密度高、单位体积占地更小，当前使用更低的保守参数：

```text
steel_warehousing_cost =
  2.0 CNY/(t-steel month)
  * min(duration_h / 720, 2 months)
  / 440 kWh/t-steel
```

seasonal/year 封顶约 `4 CNY/t-steel`，即 `0.00909 CNY/kWh`。该项同样不包含运输成本，也不包含 WACC/库存资金占用。

数据中心没有产品吨位和电耗折算，直接使用 `CNY/kW IT load` 的新增 IT 负荷资本开支：

```text
data_center_no_excess_cost =
  capex_cny_per_kw_it_load * CRF(lifetime_years) / cycles_per_year
```

这个口径与旧方案“产能超配模式”的方向一致，都是用额外固定资产避免牺牲产量或服务质量；但当前图使用更直接的 `CNY/kW load` 年化分摊，没有沿用旧文档中的闭环超配比例推导。

## 7. 用户侧：sunk excess capacity

`sunk_excess_capacity` 表示已有过剩产能、闲置设备或闲置算力。当前图把这些既有固定资产的资本成本视为沉没，因此不再分摊新建资本成本，也不计入因产量损失产生的机会成本。

不过，当前图没有把有过剩产能的成本设为零，而是保留三个代理项：

```text
sunk_excess_cost =
  fixed_floor_cny_per_kwh
  + working_capital
  + physical_warehousing_cost
  + duration_penalty_cny_per_kwh_sqrt_day * sqrt(duration_h / 24)
```

其中：

- `fixed_floor_cny_per_kwh` 表示最小调度、维护、管理摩擦的代理成本。
- `working_capital` 与 no-excess 中的库存/营运资金占用相同，按季节性生产资金成本的 WCR 面积法计算；数据中心没有该项。
- `physical_warehousing_cost` 目前加在铝和钢上。铝使用成品铝+氧化铝叠加的保守仓储上界；钢使用同一库存面积法但采用更低的单位吨月仓储参数；数据中心未计入该项。
- `duration_penalty` 表示转移时长越长，排产、库存、订单、算力任务延迟越难协调。当前用 `sqrt(duration_h / 24)` 而不是线性关系，是为了避免把长期排程摩擦放大到与新增资本成本同一量级。

当前参数：

| resource | fixed floor | duration penalty coefficient |
| --- | ---: | ---: |
| aluminium | 0.01 CNY/kWh | 0.005 CNY/kWh/sqrt(day) |
| steel | 0.02 CNY/kWh | 0.010 CNY/kWh/sqrt(day) |
| data center | 0.02 CNY/kWh | 0.012 CNY/kWh/sqrt(day) |

这个口径是对旧方案“过剩产能模式”的细化：旧方案认为资本成本与机会成本接近 0，当前图仍保留这个核心判断，但补充了非零的营运资金和调度摩擦。

## 8. 当前未计入的成本

为保持不同资源之间可比，当前图没有计入以下成本：

- 工业负荷的直接工艺成本、重启成本、设备热状态约束、质量损失。
- 企业与电网之间的通信、交易、合同、计量成本。
- 数据中心的 SLA 违约、网络迁移、数据一致性、可靠性冗余成本。
- 用户侧“直接牺牲产量/服务”的机会成本场景。
- 储能低价充电成本与高价放电收益之间的完整套利净收益；当前 storage cost 只计入效率损失对应的补电成本。

这些项目仍应在图注或正文中说明为未计入项。特别是旧方案中的“Production Restrained & Opportunity Cost”仍有经济学意义，但它更适合用于应急需求响应或停产补偿分析，不放入当前跨时间尺度可规划灵活性的主图。

## 9. 输出数据与复现

当前脚本输出三类 CSV：

| 文件 | 内容 |
| --- | --- |
| `data/flexibility_cost_timescales/figure_1_flexibility_cost_timescales_data.csv` | 每条曲线、每个时间尺度的 value/cost，单位为 CNY/kWh |
| `data/flexibility_cost_timescales/figure_1_flexibility_cost_timescales_assumptions.csv` | 汇率、折现率、电价、行业电耗、资本成本等参数 |
| `data/flexibility_cost_timescales/figure_1_flexibility_cost_timescales_sources.csv` | 本地和公开来源说明 |

复现命令：

```text
conda run -n pypsa python scripts/plot_flexibility_cost_timescales.py
```

图形输出到：

```text
results/figures/figure_1_flexibility_cost_timescales.png
results/figures/figure_1_flexibility_cost_timescales.pdf
results/figures/figure_1_flexibility_cost_timescales.svg
```
