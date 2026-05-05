<!--
SPDX-FileCopyrightText: 2026 Ruike Lyu
SPDX-License-Identifier: MIT
-->

# PyPSA-China-THU：将存量装机数据补齐到 2021–2025 的方法说明

本文档说明如何使用 `data/existing_infrastructure/查找中国各省电力装机数据.md` 中整理的“全国总量 + 部分典型省份”矩阵，更新 PyPSA-China-THU 的存量装机输入 CSV，并对缺失省份按既有装机结构进行估算分摊。

本仓库的 `data/existing_infrastructure/* capacity.csv` 采用 **分组累计 + 新增窗口** 的口径。对于新能源（光伏、风电）本次统一按：

- 先用外部口径给定“各省 2025 年末总装机”；
- 再用文件内 `2025` 之前各历史列的累计值作为基线（不是仅 `2020` 单列）；
- 写入 `2025` 列为新增量：\(\Delta_{2025}=C^{total}_{2025}-C^{cum}_{\le 2020}\)（若为负则截断为 0）。

## 1. 相关输入文件（PyPSA 读取的存量装机）

Snakemake 规则 `add_existing_baseyear` 会读取 `data/existing_infrastructure/{tech} capacity.csv`（每行一个省份、每列一个年份、单位 MW），并在 `scripts/add_existing_baseyear.py` 中把这些装机作为“基准年前已建成装机”加入网络（固定 `p_nom`、设置 `build_year` 为分组年份）。

本次更新涉及的文件为：

- `data/existing_infrastructure/solar capacity.csv`
- `data/existing_infrastructure/onwind capacity.csv`
- `data/existing_infrastructure/offwind capacity.csv`
- `data/existing_infrastructure/battery capacity.csv`（新增）
- `data/existing_infrastructure/PHS capacity.csv`（新增）
- `data/existing_infrastructure/coal capacity.csv`
- `data/existing_infrastructure/CHP coal capacity.csv`（存量供热侧燃煤 CHP，`add_existing_baseyear` 会读入）
- `data/existing_infrastructure/CHP gas capacity.csv`（存量燃气 CHP）
- `data/existing_infrastructure/OCGT capacity.csv`
- `data/existing_infrastructure/nuclear capacity.csv`

说明：

- `查找中国各省电力装机数据.md` 中给出的风电矩阵为“风电总装机（陆上+海上）”，而模型输入需要分别提供 `onwind` 与 `offwind`。
- `battery capacity.csv` 与 `PHS capacity.csv` 当前**未必**在 `config.yaml: existing_infrastructure` 列表中被引用；它们作为“可选扩展输入”提供，便于你后续把对应技术纳入 `existing_infrastructure` 或其它工作流读取。

## 2. 数据来源文件

源数据与假设整理在：

- `data/existing_infrastructure/查找中国各省电力装机数据.md`

该文档中包含：

- 全国总量（2021–2025）
- 若干典型省份（2021–2025）
- 其它省份未逐一列出（以“...余量/推算余量...”表示）

## 3. 缺失省份的估算方法（余量分摊）

对于每个技术（本次为光伏、风电总量等），先确定各省 2025 年末总装机 \(C^{total}_{p,2025}\)，再由文件历史列计算“截至 2020 的累计基线”：

\[
C^{cum}_{p,\le 2020} = \sum_{y \in Y_{hist}} C_{p,y}
\]

其中 \(Y_{hist}\) 为该文件在 `2025` 之前已有的历史列（例如光伏/海上风电是 `2010,2015,2020`；陆上风电是 `2000,2005,2010,2015,2020`）。

然后按下式写入新增量：

\[
\Delta C_{p,2025} = \max\left(C^{total}_{p,2025} - C^{cum}_{p,\le 2020},\,0\right)
\]

对缺失省份的分摊在“2025 年末总装机”层面进行：

1) 从源文档读取 2025 年全国总装机目标 \(T^{total}_{2025}\)（单位 MW）。  
2) 读取已给出省份集合 \(K\) 的 2025 年总装机并求和 \(S^{total}_{2025}=\sum_{p\in K} C^{total}_{p,2025}\)。  
3) 计算“其它省份余量”：  
\[
R^{total}_{2025} = T^{total}_{2025} - S^{total}_{2025}
\]
4) 将余量 \(R^{total}_{2025}\) 按各省“截至 2020 的累计装机基线”占比进行分摊。权重定义为：

- 光伏：使用 `solar capacity.csv` 的历史列累计值作为权重 \(w_p\)
- 风电总量：使用 `onwind capacity.csv` 与 `offwind capacity.csv` 的历史列累计值之和作为权重 \(w_p\)

分摊公式：
\[
\hat{C}^{total}_{p,2025} = R^{total}_{2025} \cdot \frac{w_p}{\sum_{q \in U} w_q}
\]
其中 \(U\) 为未在文档表格中显式给出的省份集合。

重要说明：

- **文档表格中显式列出的省份值**先确定其 `2025` 年末总装机，再按 \(\Delta C_{p,2025}=C^{total}_{p,2025}-C^{cum}_{p,\le2020}\) 写入新增量。
- **未列出的省份**先用上述 \(\hat{C}^{total}_{p,2025}\) 得到 2025 年末总装机，再按同一差分公式写入 `2025` 新增量（估算值）。
- 该方法保证“`历史累计 + 2025新增`”在全国总量上与 2025 年总装机目标一致（按构造一致）。

## 4. 风电总量拆分为 onwind / offwind 的方法

由于源文档给的是“风电总装机”，而模型需要 `onwind` 与 `offwind` 两张表：

1) 先按第 3 节得到每个省份风电 **2025 新增总量** \(\Delta W_{p,2025}\)。  
2) 用截至 2020 的累计数据计算“海上风电占比”：
\[
f_p = \frac{offwind^{cum}_{p,\le 2020}}{offwind^{cum}_{p,\le 2020}+onwind^{cum}_{p,\le 2020}}
\]
若分母为 0（该省历史累计无风电），则令 \(f_p=0\)。

3) 对 `2025` 窗口值拆分：
\[
offwind_{p,2025} = \Delta W_{p,2025} \cdot f_p,\quad onwind_{p,2025} = \Delta W_{p,2025}\cdot (1-f_p)
\]

该拆分的含义是：**保持截至 2020 的“海上/陆上比例”空间结构不变**，同时让各省 `历史累计 + 2025新增` 后的总风电与 2025 目标匹配。

## 5. 这次更新实际写入了哪些内容

已将下列文件新增/更新了 `2025` 列（2025 新增量，MW）：

- `data/existing_infrastructure/solar capacity.csv`
- `data/existing_infrastructure/onwind capacity.csv`
- `data/existing_infrastructure/offwind capacity.csv`
- `data/existing_infrastructure/battery capacity.csv`（新增）
- `data/existing_infrastructure/PHS capacity.csv`（新增）
- `data/existing_infrastructure/coal capacity.csv`（新增 `2025` 列）
- `data/existing_infrastructure/CHP coal capacity.csv`（新增 `2025` 列）
- `data/existing_infrastructure/CHP gas capacity.csv`（新增 `2025` 列）
- `data/existing_infrastructure/OCGT capacity.csv`（新增 `2025` 列）
- `data/existing_infrastructure/nuclear capacity.csv`（新增 `2025` 列）

其中：

- 光伏：按“各省 2025 总装机 - 历史列累计基线”写入 `2025` 新增；其余省份先分摊 2025 总装机再差分。
- 风电：先确定各省 2025 风电总装机（陆+海），再减去 `onwind/offwind` 历史列累计得到新增总量，并按历史海上占比拆分到 onwind/offwind。
- 电池（新型储能）：`battery capacity.csv` 的 `2025` 列按国家能源局 2025 年底口径重标定（全国 1.36 亿千瓦）；其中内蒙古/新疆/山东分别锚定为 2026/1880/1121 万千瓦，其余省份按既有分布缩放并满足“河北、江苏、宁夏、云南、甘肃、浙江、河南、广东装机规模超 500 万千瓦”的公开约束。
- 抽水蓄能（PHS）：`PHS capacity.csv` 的 `2025` 列按国家能源局 2025 年底口径重标定为全国 **6594 万千瓦（65.94GW）**，省际分布保持与原始分配权重一致（同比例缩放）。
- 火电（coal/OCGT）：以国家口径火电装机“2025 年末 1539.04GW - 2020 年末 1245.17GW”得到 2021–2025 新增规模，并按 `coal capacity.csv` 与 `OCGT capacity.csv` 的 2020 列占比在两者之间分摊；省内再按各自 2020 列占比分摊（估算）。
- **煤电 CHP（`CHP coal capacity.csv`）**：`查找中国各省电力装机数据.md` 对 CHP 仅有政策与定性讨论，没有与 `coal capacity.csv` 同级的逐年省际矩阵，因此上一轮只把并网煤电 CSV 补齐到 `2025`。若基准年设为 2025，缺列会导致脚本读不到增量。现为与模型内部口径自洽：**先按本节火电方法得到各省 `coal capacity.csv` 的 `2025` 窗口**，再假定 CHP 在“煤电存量”中与纯凝煤电按 **2020 年存量结构比例**同步扩张：令 \(\Sigma_{coal}\)、\(\Sigma_{CHP}\) 分别为同一批省份上 `coal` 与 `CHP coal` 的 **2020 列**之和，\(T=\sum_p \Delta coal_{p,2025}\) 为这些省份 `coal capacity.csv` 的 `2025` 窗口合计，则煤电 CHP 全国窗口增量近似为 \(\gamma\,T\)，其中 \(\gamma=\Sigma_{CHP}/(\Sigma_{coal}+\Sigma_{CHP})\)；省内分摊取 \(\Delta CHP_{p,2025}=\gamma\,T\cdot CHP_{p,2020}/\Sigma_{CHP}\)，**2020 年本省 CHP 为 0 的省份在本次估算中亦为 0**（明确简化假设）。
- **气电 CHP（`CHP gas capacity.csv`）**：同样在源文档中缺少与 `OCGT capacity.csv` 对齐的省际矩阵。与煤电 CHP 对称：令 \(\Sigma_{OCGT}\) 为全表 **`OCGT capacity.csv` 的 2020 列**之和，\(\Sigma_{gasCHP}\) 为 **`CHP gas capacity.csv` 的 2020 列**之和，\(U=\sum_p \Delta OCGT_{p,2025}\) 为 `OCGT capacity.csv` 的 `2025` 窗口合计，则 \(\gamma_{gas}=\Sigma_{gasCHP}/(\Sigma_{OCGT}+\Sigma_{gasCHP})\)，全国气电 CHP 窗口增量取 \(\gamma_{gas}\,U\)，省内分摊 \(\Delta gasCHP_{p,2025}=\gamma_{gas}\,U\cdot gasCHP_{p,2020}/\Sigma_{gasCHP}\)（权重为 0 的省亦为 0）。**局限**：\(\Sigma_{OCGT}+\Sigma_{gasCHP}\) 仅占模型所收录省份，且与全国性气电快报不等价；若要更写实，宜用全国气电/分布式气电分项替换 \(U\) 与本表分摊。
- 核电：以“2025 年末 62.52GW - 2020 年末 49.89GW”得到 2021–2025 新增规模，并按 `nuclear capacity.csv` 的 2020 列省际占比分摊（估算）。

电池能量容量换算说明：

- 本仓库的 `battery capacity.csv` 记录的是 **功率容量 \(P\)**（MW），在导入网络时需要换算为电量容量 \(E\)（MWh）。
- **存量电池**的换算采用“全国平均储能时长”（截至 2025 年底 **2.58 小时**），而不是模型里用于新建电池的默认 `max_hours`（例如 6 小时）。换算公式为：
\[
E = P \times 2.58
\]
- 该 2.58h 来源：国家能源局新闻发布会《国家能源局举行新闻发布会介绍2025年新型储能发展情况》（2026-01-30）明确给出“平均储能时长 2.58 小时”。见下方链接。

## 6. 注意事项与局限性

- 本方法对未列出省份/未提供完整省级矩阵的技术属于**估算**，适用于在缺少完整统计表时，快速将基准窗口平移到 2025 的工程化处理。
- 若未来获得“所有省份完整矩阵”，建议直接覆盖写入，替代余量分摊/代理权重分摊。
- 对煤电/气电/抽蓄/核电等技术，本次使用了全国总量差分与 2020 省际结构的组合估算；**煤电 CHP、气电 CHP**的 `2025` 列为省际统计缺失下的**推导量**（分别按 2020 结构从煤电/OCGT 的窗口增量比例切分），请在发表/汇报时明确其不确定性。其它热力侧存量（如 **`coal boiler`、热泵等**）若仍只列至历史末年而基准年又要求对应列存在，同样需要补齐或暂时从 `existing_infrastructure` 去掉。

## 7. 外部数据来源链接（本次用到的“全国口径”锚点）

下面链接对应的是本次在 `readme_cn.md` 中写到的全国口径锚点（用于把 2021–2025 窗口的“新增规模”约束到一个合理的总量水平），与 `查找中国各省电力装机数据.md` 中的文字叙述口径保持一致/兼容。

- **2020 年全国电力工业统计数据（含 2020 年末火电、核电装机）**：`https://www.nea.gov.cn/2021-01/20/c_139683739.htm`
- **2025 年全国电力统计数据（含 2025 年末全国装机、风电/光伏等）**：`https://www.nea.gov.cn/20260129/6874f211acd0417eab7ac10c3061a7c2/c.html`
- **2025 年核电在运装机（截至 2025-12-31：62518.74 MWe）**：`https://nnsa.mee.gov.cn/ywdt/hyzx/202602/t20260206_1143783.html`
- **（转载同文）国家能源局发布 2025 年全国电力统计数据（生态环境部站点镜像）**：`https://nnsa.mee.gov.cn/ywdt/hyzx/202601/t20260129_1142949.html`
- **2025 年新型储能平均储能时长 2.58 小时（截至 2025 年底）**：`https://www.nea.gov.cn/20260130/50f657ce87f848e1a9a1861d1fd9aa23/c.html`

说明：

- 抽水蓄能 “2025 年约 65GW” 本次采用的是 `查找中国各省电力装机数据.md` 中给出的保守估计，并用仓库内 `data/hydro/PHS_p_nom.csv` 的省际分布作权重分摊；若你后续提供更权威的全国抽蓄口径/链接，可以替换这里的假设与分摊总量。

## 8. 新能源成本（`data/costs/costs_*.csv`）的 2025 校准与外推方法

本仓库的优化工作流会读取 `data/costs/costs_{year}.csv` 作为技术经济参数表（包括 `investment`、`FOM`、`VOM` 等）。其中 `investment` 是模型里最敏感、也最容易因“国家/口径不一致”而偏离现实的参数。

为避免直接使用欧洲数据源（例如 Danish Energy Agency, DEA）导致中国情景下成本显著失真，我们对下列新能源技术的 **`investment`（CAPEX）** 做了“2025 中国口径”校准，并把后续年份按原始表格给定的相对下降趋势同步缩放（保持学习曲线/趋势不变）。

### 8.1 校准对象

- 风电：`onwind`、`offwind`
- 光伏：`solar-utility`、`solar-rooftop`
- 电化学储能：`battery storage`（能量端，EUR/kWh）、`battery inverter`（功率端，EUR/kW）

注：本次只校准 `investment`。`FOM/VOM/lifetime/efficiency` 等参数仍沿用原表来源（后续如需完全中国化，可再单独替换并明确口径）。

### 8.2 2025 年锚点（我采用的“最合理”公开口径）

为和国内公开数据更可比，这里优先采用“工程造价/EPC/静态投资”量级作为锚点（而不是仅设备价）。

- **陆上风电**：取 2025 “平原+山地”区间的中位水平，约 **3800 元/kW**
- **海上风电**：取典型项目量级 **10000 元/kW**
- **集中式光伏（地面）**：参考 2025 年地面电站 EPC 中标均价量级，约 **2.7 元/W（=2700 元/kW）**
- **分布式屋顶光伏**：考虑场景复杂度与系统成本更高，取 **3.5 元/W（=3500 元/kW）**
- **电化学储能**：参考 2025Q4–2026Q2 国内系统报价（两小时直流侧液冷系统约 0.41–0.49 元/Wh），并将其拆分为“能量端 + 功率端”的可优化形式：
  - `battery storage`：**260 元/kWh**
  - `battery inverter`：**300 元/kW**

公开来源（用于上述量级约束）：

- 水电水利规划设计总院《中国可再生能源工程造价管理报告2024年度》相关报道汇总（陆上/海上风电单位造价区间）：`http://windpower.cpem.org.cn/contents/31/2312.html`
- SMM 光伏电站 EPC 中标价格统计（地面电站 EPC 均价量级）：`https://news.smm.cn/news/103702271`
- InfoLink/ESS News 对国内储能电芯与系统价格的跟踪（系统价从 2025Q4 到 2026Q2 的量级与波动）：`https://www.ess-news.com/2026/04/22/chinas-314-ah-storage-cell-prices-climb-more-than-20-in-six-months/`

### 8.3 从 2025 外推到其它年份的方法（保持原趋势）

对每个技术 \(t\) 的 `investment`，记原表为 \(I^{old}_{t,y}\)，校准后的 2025 值为 \(I^{new}_{t,2025}\)。则对任意年份 \(y\)：

\[
I^{new}_{t,y} = I^{old}_{t,y} \cdot \\frac{I^{new}_{t,2025}}{I^{old}_{t,2025}}
\]

这样做的效果是：

- 2025 年严格对齐中国公开锚点；
- 2025 之后的下降幅度（学习率/趋势）与原表一致；
- 不同技术之间的相对趋势不会被重新“拍脑袋”改写。

### 8.4 汇率与单位说明

成本表内部单位为 EUR（如 `EUR/kW`、`EUR/kWh`）。为了把上述人民币锚点写入表格，本次采用固定换算：

- \(1\,EUR \approx 7.8\,CNY\)

该换算仅用于把“人民币锚点”映射到成本表内部单位，便于建模；不用于金融分析或汇率预测。
