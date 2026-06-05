# 灵活性资源跨循环周期成本计算方法说明

本文档说明 `scripts/plot_flexibility_cost_timescales.py` 当前采用的计算逻辑。所有曲线统一为：

$$
\mathrm{CNY/kWh\ shifted}
$$

其中 `system` 曲线是单位平移电量可获得的系统价值，其他资源曲线是单位平移电量成本。

## 1. 循环周期

当前图使用五个时间窗口。每个窗口内都理解为“从低 marginal price 时段搬到高 marginal price 时段”。

| cycle period | window / duration | cycles per year | 含义 |
| --- | ---: | ---: | --- |
| daily | 24 h | 365 | 每天一个窗口 |
| weekly | 168 h | 52.14 | 每周一个窗口 |
| monthly | 730 h | 12.00 | 每月约一个窗口 |
| seasonal | 2160 h | 4.06 | 约 3 个月一个窗口 |
| annual | 8760 h | 1.00 | 全年一个窗口 |

统一定义为：

$$
N_{\mathrm{cycle}}=\frac{8760}{T_{\mathrm{window}}}.
$$

循环次数只是中间分摊变量。对固定资产成本，真正的分母是全年可平移电量：

$$
E_{\mathrm{shift,yr}}=P_{\mathrm{flex}}\cdot T_{\mathrm{shift}}\cdot N_{\mathrm{cycle}}.
$$

当前脚本中 \(T_{\mathrm{shift}}=T_{\mathrm{window}}\)。如果后续显式设置“只避开窗口内 top \(r\) 比例时段”，则应改为：

$$
T_{\mathrm{shift}}=rT_{\mathrm{window}}.
$$

## 2. System Value

系统价值使用 marginal price 价差，而不是 mapped price，也不是单独的高价均值。

数据源：

| 曲线 | 数据源 | 单位处理 |
| --- | --- | --- |
| system value, 2025 | CESC 2025 中国省级现货市场日内价差报道 | 采用中国现货市场典型日内价差代理值 0.45 CNY/kWh |
| system value, 2050 CN | `results/version-0120.1H.1-MMMF-2050-15p/postnetworks/positive/postnetwork-ll-current+FCG-linear2050-2050.nc` | 读取 31 个 AC 省级 bus 的 `buses_t.marginal_price`，按 7.8 CNY/EUR 从 EUR/MWh 转为 CNY/kWh |

2025 曲线不是本地 PyPSA 模型结果，而是公开现货市场观测代理值。CESC 报道中，山东 2025 年现货市场日内峰谷价差约 449 CNY/MWh，辽宁、山东、湖南等中波动省份大致处在 300-500 CNY/MWh 区间。当前图取四舍五入后的：

$$
V_{\mathrm{system,2025}}=0.45\ \mathrm{CNY/kWh}.
$$

该值作为 2025 当前市场日内价差代理，应用到所有循环周期；它不代表 2025 长周期系统优化价值。

2050 曲线来自本地 carbon-neutral postnetwork。对每个窗口 \(W\)，取最高的 \(H\) 小时和最低的 \(H\) 小时：

$$
V_W=
\overline p_{\mathrm{top},H,W}
-
\overline p_{\mathrm{bottom},H,W}.
$$

然后对所有窗口和 bus 求平均：

$$
V_{\mathrm{system}}=
\mathrm{mean}_W(V_W).
$$

当前 \(H\) 设置为：

| cycle period | window | H |
| --- | ---: | ---: |
| daily | 24 h | 2.6 h |
| weekly | 168 h | 24 h |
| monthly | 730 h | 168 h |
| seasonal | 2160 h | 168 h |
| annual | 8760 h | 168 h |

因为 system value 已经是高低价差：

$$
V_{\mathrm{system}}=
\bar p_{\mathrm{high}}-\bar p_{\mathrm{low}},
$$

成本线中不再额外加入低价补产电价，否则会重复扣除低价用能成本。

## 3. Battery Storage Cost

电池成本来自 `data/costs/costs_2025.csv` 中的：

- `battery storage`: energy capacity investment, lifetime
- `battery inverter`: power capacity investment, lifetime, FOM, efficiency

单位搬运成本为：

$$
c_{\mathrm{battery}}=
\frac{
K_E\mathrm{CRF}_E
+
\frac{K_P}{T_{\mathrm{window}}}(\mathrm{CRF}_P+\mathrm{FOM}_P)
}{N_{\mathrm{cycle}}}
+
p_{\mathrm{loss}}\left(\frac{1}{\eta_{\mathrm{rt}}}-1\right).
$$

其中：

- \(K_E\): energy capacity cost, CNY/kWh
- \(K_P\): power capacity cost, CNY/kW
- \(p_{\mathrm{loss}}=0.30\) CNY/kWh
- \(\eta_{\mathrm{rt}}\): round-trip efficiency

## 4. Hydrogen Storage Cost

氢储能按 power-to-hydrogen-to-power 链条计算，成本来自：

- `electrolysis`
- `fuel cell`
- `hydrogen storage underground`

对输出端 1 kWh 电量，容量需求为：

$$
k_{\mathrm{ely}}=
\frac{1}{\eta_{\mathrm{ely}}\eta_{\mathrm{fc}}T_{\mathrm{window}}},
\quad
k_{\mathrm{fc}}=
\frac{1}{T_{\mathrm{window}}},
\quad
e_{\mathrm{h2}}=
\frac{1}{\eta_{\mathrm{fc}}}.
$$

单位搬运成本为：

$$
c_{\mathrm{h2}}=
\frac{
k_{\mathrm{ely}}K_{\mathrm{ely}}(\mathrm{CRF}_{\mathrm{ely}}+\mathrm{FOM}_{\mathrm{ely}})
+
k_{\mathrm{fc}}K_{\mathrm{fc}}(\mathrm{CRF}_{\mathrm{fc}}+\mathrm{FOM}_{\mathrm{fc}})
+
e_{\mathrm{h2}}K_{\mathrm{store}}(\mathrm{CRF}_{\mathrm{store}}+\mathrm{FOM}_{\mathrm{store}})
}{N_{\mathrm{cycle}}}
+
p_{\mathrm{loss}}
\left(
\frac{1}{\eta_{\mathrm{ely}}\eta_{\mathrm{fc}}}-1
\right).
$$

## 5. No Excess Capacity

`no_excess_capacity` 表示没有闲置产能或闲置算力。为了提供灵活性，需要新增可用负荷能力，并将新增固定资产成本摊到全年 shifted energy 上。

### 5.1 工业负荷资本成本

铝和钢先把产能资本开支从 `CNY/(t/year)` 转为 `CNY/kW load`：

$$
K_C=
\frac{K_{\mathrm{tonne/year}}}
{e_{\mathrm{intensity}}/8760}.
$$

然后按全年 shifted energy 分摊：

$$
c_{\mathrm{cap,no\ excess}}=
\frac{K_C\cdot\mathrm{CRF}}
{N_{\mathrm{cycle}}T_{\mathrm{window}}}.
$$

### 5.2 数据中心资本成本

数据中心直接使用 `CNY/kW IT load`：

$$
c_{\mathrm{dc,no\ excess}}=
\frac{K_{\mathrm{IT}}\cdot\mathrm{CRF}}
{N_{\mathrm{cycle}}T_{\mathrm{window}}}.
$$

### 5.3 库存资金占用

铝和钢另加库存资金占用。资金占用与物理仓储分开计算。

资金锁定时间为：

$$
\tau_{\mathrm{wc}}=
\max
\left(
\frac{T_{\mathrm{window}}}{8760},
\frac{0.5}{N_{\mathrm{cycle}}}
\right).
$$

单位 shifted energy 的资金占用成本为：

$$
c_{\mathrm{wc}}=
\frac{
P_{\mathrm{product}}\cdot r_{\mathrm{discount}}\cdot \tau_{\mathrm{wc}}
}{e_{\mathrm{intensity}}}.
$$

### 5.4 物理仓储成本

物理仓储成本按库存面积法理解：

$$
\mathrm{Inv}(t)=
\mathrm{cumsum}(\mathrm{Production})
-
\mathrm{cumsum}(\mathrm{Demand}),
$$

$$
\mathrm{Area}(t)=
\frac{\mathrm{Inv}(t)}
{\rho_{\mathrm{material}}\cdot h_{\mathrm{effective}}\cdot \eta_{\mathrm{storage}}},
$$

$$
\mathrm{Storage\ cost}=
\mathrm{Area}\cdot \mathrm{rental\ rate}\cdot \mathrm{time}.
$$

当前脚本不逐时重建库存面积，而使用保守吨月参数。

铝：

$$
c_{\mathrm{wh,Al}}=
\frac{
6.9\ \mathrm{CNY/(t\ month)}
\cdot
\min(T_{\mathrm{window}}/720,2)
}{13300\ \mathrm{kWh/t}}.
$$

钢：

$$
c_{\mathrm{wh,steel}}=
\frac{
2.0\ \mathrm{CNY/(t\ month)}
\cdot
\min(T_{\mathrm{window}}/720,2)
}{440\ \mathrm{kWh/t}}.
$$

### 5.5 No-Excess 总成本

铝和钢：

$$
c_{\mathrm{no\ excess}}=
c_{\mathrm{cap}}
+
c_{\mathrm{wc}}
+
c_{\mathrm{wh}}.
$$

数据中心：

$$
c_{\mathrm{no\ excess,dc}}=
c_{\mathrm{cap,dc}}.
$$

当前没有计入工业工艺重启、产品质量、通信交易、数据中心 SLA 等直接成本。

## 6. Sunk Excess Capacity

`sunk_excess_capacity` 表示已有闲置产能或闲置算力，固定资产资本成本视为沉没，不再分摊新增资本成本。

铝和钢：

$$
c_{\mathrm{sunk}}=
c_{\mathrm{floor}}
+
c_{\mathrm{wc}}
+
c_{\mathrm{wh}}
+
c_{\mathrm{delay}}.
$$

数据中心：

$$
c_{\mathrm{sunk,dc}}=
c_{\mathrm{floor}}
+
c_{\mathrm{delay}}.
$$

调度延迟代理成本为：

$$
c_{\mathrm{delay}}=
\alpha\sqrt{\frac{T_{\mathrm{window}}}{24}}.
$$

当前参数：

| resource | fixed floor | delay coefficient |
| --- | ---: | ---: |
| aluminium | 0.01 CNY/kWh | 0.005 CNY/kWh/sqrt(day) |
| steel | 0.02 CNY/kWh | 0.010 CNY/kWh/sqrt(day) |
| data center | 0.02 CNY/kWh | 0.012 CNY/kWh/sqrt(day) |

## 7. 过剩产能比例的解释

如果已有过剩产能比例为 \(r_C\)，实际可用产能为：

$$
C_{\mathrm{available}}=(1+r_C)C.
$$

在需求 \(D=CT\) 不变时，需要运行的时间为：

$$
T_{\mathrm{run}}=
\frac{T}{1+r_C}.
$$

可避开的高价时间占比为：

$$
\rho=
\frac{T-T_{\mathrm{run}}}{T}
=
\frac{r_C}{1+r_C}.
$$

若后续希望把 overcapacity ratio 显式接入价格选择，则应在每个窗口内取 top \(\rho\) 比例价格作为避开时段，取 bottom 或剩余时段作为补产时段。当前脚本暂时使用固定的 \(H\) 小时规则，而不是显式输入 \(r_C\)。

## 8. 输出文件

运行：

```text
conda run -n pypsa python scripts/plot_flexibility_cost_timescales.py
```

输出数据：

| 文件 | 内容 |
| --- | --- |
| `data/flexibility_cost_timescales/figure_1_flexibility_cost_timescales_data.csv` | 每条曲线、每个循环周期的 value/cost |
| `data/flexibility_cost_timescales/figure_1_flexibility_cost_timescales_assumptions.csv` | 汇率、折现率、电耗、资本成本、仓储参数等 |
| `data/flexibility_cost_timescales/figure_1_flexibility_cost_timescales_sources.csv` | 数据来源 |

输出图：

```text
results/figures/figure_1_flexibility_cost_timescales.png
results/figures/figure_1_flexibility_cost_timescales.pdf
results/figures/figure_1_flexibility_cost_timescales.svg
```
