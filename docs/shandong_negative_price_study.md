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
