# 储能套利评估方法说明（对应 `evaluate_storage_cycles.py`）

本文档说明 `scripts/evaluate_storage_cycles.py` 使用的简化储能套利评估公式与计算口径。

## 1. 参数与数据来源

给定已求解的 PyPSA 网络文件 `nc_file_path`，目标省份母线 `target_bus`，以及电池组件名：

- Store：`store_name`
- 充电 Link：`charge_link`
- 放电 Link：`discharge_link`

脚本动态提取：

1. 电价序列（按小时）  
   \[
   p_t^{CNY} = n.buses\_t.marginal\_price[target\_bus]_t \cdot fx_{CNY/EUR},\quad t=1,\dots,T
   \]
   其中 `T` 为网络快照长度（通常为 8760），`fx_{CNY/EUR}` 默认取 7.8。

2. 充放电效率  
   \[
   \eta_{in} = n.links.loc[charge\_link,\ "efficiency"]
   \]
   \[
   \eta_{out} = n.links.loc[discharge\_link,\ "efficiency"]
   \]

3. 年化能量投资成本（按 MWh-year）  
   \[
   C_{annual}^{CNY} = n.stores.loc[store\_name,\ "capital\_cost"] \cdot fx_{CNY/EUR}
   \]

4. 优化容量与充放电时长 \(H\)  
   \[
   E_{cap}=
   \begin{cases}
   e\_nom\_opt,& \text{若存在且非空}\\
   e\_nom,& \text{否则}
   \end{cases}
   \]
   \[
   P_{cap}=
   \begin{cases}
   p\_nom\_opt,& \text{若存在且非空}\\
   p\_nom,& \text{否则}
   \end{cases}
   \]
   \[
   H=\mathrm{round}\!\left(\frac{E_{cap}}{P_{cap}}\right)
   \]
   若 \(P_{cap}\le 0\) 或 \(H\le 0\)，脚本会报错终止。

---

## 2. 分块循环定义

定义窗口长度 \(W\in\{24,168,730,8760\}\)（日/周/月/年）。

按小时序号 \(i=0,\dots,T-1\) 构造分块编号：
\[
chunk(i)=\left\lfloor \frac{i}{W}\right\rfloor
\]

同一 `chunk_id` 内视作一个独立循环块。

---

## 3. 单个循环块内的选时与毛利

对每个块 \(c\)：

- 取该块中电价最高的 \(H\) 个小时集合 \(Top_H(c)\)
- 取该块中电价最低的 \(H\) 个小时集合 \(Bottom_H(c)\)

### 3.1 放电收益
\[
R_c=\eta_{out}\sum_{t\in Top_H(c)} p_t^{CNY}
\]

### 3.2 充电成本
\[
K_c=\frac{1}{\eta_{in}}\sum_{t\in Bottom_H(c)} p_t^{CNY}
\]

### 3.3 块毛利（允许不动作）
\[
G_c=\max(R_c-K_c,\ 0)
\]
若价差不足以覆盖效率损耗，则该块不进行循环，毛利记为 0。

---

## 4. 年度指标

设全年块数为 \(N_{cycle}\)（即 `chunk_id` 的唯一值个数）。

### 4.1 年度总毛利
\[
G_{year}=\sum_{c=1}^{N_{cycle}} G_c
\]

### 4.2 年度固定投资成本（1MW/H MWh 单元）
\[
Cost_{year}=C_{annual}^{CNY}\cdot H
\]

### 4.3 年度净收益
\[
Net_{year}=G_{year}-Cost_{year}
\]

---

## 5. 单位搬运成本/效益（用于第二张图）

脚本对每个窗口 \(W\) 还计算单位搬运（每 1MWh shifted）的统计量。

1. 先在每个块内求 top-\(H\) 与 bottom-\(H\) 均价，再对所有块取平均：
\[
\bar p_{top}^{CNY}=\frac{1}{N_{cycle}}\sum_{c=1}^{N_{cycle}} \left(\frac{1}{H}\sum_{t\in Top_H(c)}p_t^{CNY}\right)
\]
\[
\bar p_{bot}^{CNY}=\frac{1}{N_{cycle}}\sum_{c=1}^{N_{cycle}} \left(\frac{1}{H}\sum_{t\in Bottom_H(c)}p_t^{CNY}\right)
\]

2. 单位效益（放电侧）：
\[
Benefit_{unit}=\bar p_{top}^{CNY}\cdot \eta_{out}
\]

3. 单位电能成本（充电侧）：
\[
Cost_{unit,energy}=\frac{\bar p_{bot}^{CNY}}{\eta_{in}}
\]

4. 投资成本按“年循环次数”摊销：
\[
Capex_{cycle}=\frac{Cost_{year}}{N_{cycle}}
\]
折算到每 1MWh：
\[
Cost_{unit,capex}=\frac{Capex_{cycle}}{H}
\]

5. 单位总成本：
\[
Cost_{unit,total}=Cost_{unit,energy}+Cost_{unit,capex}
\]

---

## 6. 图表口径

1. 图 1：`annual_net` 柱状图（各窗口年度净收益）。
2. 图 2：同一纵轴（对数刻度）展示：
   - 柱：`unit_benefit`
   - 线：`unit_cost_total`

---

## 7. 注意事项

- 这是“理论套利上限”近似法，忽略了块内严格时序可行性（SOC 动态约束）与功率轨迹耦合，仅用于快速比较不同循环窗口的价差潜力。
- 当 `matplotlib` 不可用时，脚本会跳过绘图但仍返回结果表。

---

## 8. 符号表（人民币口径）

| 符号 | 含义 | 单位 |
|---|---|---|
| \(p_t^{CNY}\) | 第 \(t\) 小时电价（换算到人民币） | CNY/MWh |
| \(\eta_{in}\) | 充电效率 | 无量纲 |
| \(\eta_{out}\) | 放电效率 | 无量纲 |
| \(E_{cap}\) | 电池能量容量 | MWh |
| \(P_{cap}\) | 充电功率上限 | MW |
| \(H\) | 充放电小时数，\(H=\mathrm{round}(E_{cap}/P_{cap})\) | h |
| \(W\) | 分块窗口长度（日/周/月/年） | h |
| \(N_{cycle}\) | 年循环次数（块数） | 次/年 |
| \(C_{annual}^{CNY}\) | 单位能量容量年化投资成本 | CNY/(MWh·年) |
| \(R_c\) | 第 \(c\) 块放电收益 | CNY |
| \(K_c\) | 第 \(c\) 块充电成本 | CNY |
| \(G_c\) | 第 \(c\) 块毛利（负值截断为0） | CNY |
| \(G_{year}\) | 年度总毛利 | CNY/年 |
| \(Cost_{year}\) | 年度固定投资成本 | CNY/年 |
| \(Net_{year}\) | 年度净收益 | CNY/年 |
| \(Capex_{cycle}\) | 单次循环摊销投资成本 | CNY/次 |
| \(Benefit_{unit}\) | 每搬运 1MWh 的单位收益 | CNY/MWh |
| \(Cost_{unit,energy}\) | 每搬运 1MWh 的电能成本 | CNY/MWh |
| \(Cost_{unit,capex}\) | 每搬运 1MWh 的投资摊销成本 | CNY/MWh |
| \(Cost_{unit,total}\) | 每搬运 1MWh 的总成本 | CNY/MWh |
