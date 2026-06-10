# Shandong Negative-Price Study Setup

This setup prepares a dispatch-only negative-price study for Shandong using
`results/version-0609.1H.1` as the capacity source.

## Core assumptions

- Province: Shandong only.
- Anchor planning years: 2025, 2030, 2035.
- Study years: 2027-2035.
- Intermediate-year capacities: linear interpolation between adjacent anchor years.
- Renewable bids: onshore wind, offshore wind and solar bid at `-80 CNY/MWh`.
- Thermal segmented bids: first thermal segment is treated as the minimum-output
  tranche and bids at `0 CNY/MWh`; later segments keep the fuel-cost-based
  segmented bid curve.
- Bid bounds: `[-80, 1300] CNY/MWh`.
- Price export: negative prices are retained and clipped only to the configured
  market floor/cap.
- Low-output negative-price rule: compare thermal output against 40% of the
  reserve-adjusted weekly maximum thermal output (`W-SUN`), rather than the
  daily maximum. The current reserve margin is `15%`, so the cutoff is:
  `weekly max thermal output * 1.15 * threshold`.

## External exchange / net receiving load deduction

The model version in use is a Shandong single-node setup, so interprovincial
exports are not represented as solved network flows. Per the updated study
method, Shandong's net received electricity is represented as a load deduction
instead of an export-capacity sensitivity.

- High scenario annual net receiving assumptions:
  - 2025: `1500 亿千瓦时/year` (`150 TWh/year`).
  - 2030: `2200 亿千瓦时/year` (`220 TWh/year`).
  - 2035: `2600 亿千瓦时/year` (`260 TWh/year`).
- Implementation: subtract the deduction from Shandong AC load before the
  dispatch solve, allocated hour by hour in proportion to wind/PV available
  output before curtailment.
- Concentration control: hourly receiving capacity scales in proportion to the
  annual net-receiving energy assumption:
  - 2025: `33.0 GW`.
  - 2030: `48.4 GW`.
  - 2035: `57.2 GW`.
  The adjusted local load must also retain at least `10%` of the original load.
  Remaining annual net-receiving energy is redistributed to other high-wind/PV
  hours until the annual target is met or the caps are exhausted.

This remains a configurable assumption and should be replaced by a final
statistical value if a better 2025 net-receiving data point is selected.

Reference points:

- Shandong spot-market energy bid bounds are reported as `-80 to 1300 CNY/MWh`
  in market commentary and news coverage of Shandong's negative-price episodes:
  https://www.stcn.com/article/detail/861987.html
- Public reports on "外电入鲁" state that Shandong's out-of-province receiving
  capability has increased to about `3300 万千瓦`; this is a receiving/import
  capability reference, not a direct firm export capacity for Shandong surplus
  renewable power: https://news.iqilu.com/shandong/shandonggedi/20250312/5787552.shtml

## Two-stage thermal minimum-output adjustment

The thermal minimum-output treatment follows a two-stage approximation:

1. Stage 1: dispatch is solved with the thermal minimum-output tranche bidding
   at `0 CNY/MWh`.
2. Stage 2: a postprocessor checks each hour. If synchronous/thermal output is
   below the configured minimum-output requirement, the deficit is filled by
   replacing wind/PV dispatch with thermal output.
3. The wind/PV curtailment amount is allocated in proportion to the original
   dispatched wind/PV output in that hour.

The postprocessor writes adjusted thermal output, adjusted VRE output,
curtailment by carrier, and a minimum-output binding flag.

## Prepared files

- `configs/shandong_negative_price_0609.1H.1.yaml`: scenario overlay for review.
- `data/shandong_negative_price/market_assumptions.csv`: compact parameter table.
- `scripts/prepare_shandong_negative_price_inputs.py`: reads anchor-year
  postnetworks and writes interpolated capacity inputs.
- `scripts/apply_shandong_two_stage_min_output.py`: reads a stage-1 dispatch
  network and writes the two-stage minimum-output adjustment table.

## Suggested confirmation step

Run only the preparation script first:

```bash
conda run -n pypsa python scripts/prepare_shandong_negative_price_inputs.py
```

This writes CSV inputs and does not solve the dispatch model.


本研究采用“长期规划模型 + 市场出清模拟”的两阶段方法评估山东高新能源渗透率下的负电价频率和新能源收益变化。首先，利用长期电力系统规划模型得到不同年份的山东电源装机结构，包括风电、光伏、煤电、核电、储能等资源配置；随后固定规划模型给出的装机容量，构建小时级市场出清模拟，用于刻画现货市场价格形成过程。

在报价行为方面，模型假设风电和光伏作为低边际成本电源参与市场报价，并在负电价研究情景中按山东现货市场价格下限报价，即 -80 元/MWh。常规火电机组采用分段报价方式，将可用容量拆分为多个报价段，以近似真实市场中煤电机组随出力上升而逐步提高报价的行为。其中，火电最小出力对应的基础出力段按 -80 元/MWh 报价，用于反映保障系统稳定运行所需的刚性同步机出力。后续更高出力段则基于燃料成本和报价倍率形成递增报价曲线。市场价格设有报价上下限，山东情景中采用 -80 至 1300 元/MWh 的价格边界。系统模拟方面，模型在每个小时内根据负荷、新能源可用出力、常规机组容量、储能和系统约束进行经济调度，得到山东节点的市场出清价格，火电最小出力为开机容量的40%。

规划模型与市场出清模型的耦合关系为：规划模型决定中长期装机结构和资源规模，市场出清模型在给定装机基础上模拟小时级运行和价格形成。对于 2027-2035 年，研究基于 2025、2030、2035 年规划结果进行线性插值，得到各中间年份的装机容量，并据此开展现货价格和新能源收益测算。新能源渗透率采用山东发电装机口径计算，即风电和光伏装机占山东电力侧可用总装机的比例。

新能源度电收益进一步结合现货市场收益和机制电量收益计算。具体而言，风电和光伏的现货收益由其小时出力与小时市场价格加权得到；机制收益则按照给定机制电价和机制电量比例计入。最终度电收益按“机制电量比例 × 机制电价 + 现货电量比例 × 现货加权电价”计算，并分别区分存量项目和增量项目。这样可以同时反映高新能源渗透率下现货价格下降、负电价增加以及机制电量保障对新能源项目收益的影响。

