# 2025 Solar Value Dataset Calculation Method

This document describes the current calculation method used to fill 2025 rows in:

- `results/version-0505.1H.2/solar_value_dataset.xlsx`

The implementation script is:

- `scripts/fill_solar_value_dataset_2025.py`

## 1) Data Sources

- Dispatch network:
  - `results/version-0505.1H.2/dispatch_segmented/positive/postnetwork-dispatch-seg-ll-current+FCG-linear2050-2025.nc`
- Nodal price (mapped):
  - `results/version-0505.1H.2/prices/dispatch_segmented/positive/dispatch_segmented_prices-ll-current+FCG-linear2050-2025_mapped.csv`
- Real solar capacity input:
  - `data/existing_infrastructure/solar capacity.csv`

## 2) Province Name Mapping

Workbook province names are mapped to model/price names as:

- `Xizang -> Tibet`
- `WestInnerMongolia -> InnerMongolia`
- `EastInnerMongolia -> InnerMongolia`

## 3) Which Rows Are Filled

Only the first province block (rows `2-33`) is filled for year 2025.

- `year` column is set to `2025`.
- Metric columns filled:
  - `solar_ele_GWh`
  - `value_factor_numerator`
  - `value_factor_denominator`
  - `value_factor`
  - `solar_penetration`
  - `solar_curtailment_rate`
  - `solar_capacity_factor`

## 4) Real Capacity Treatment (2025 Special Handling)

The real solar capacity used for scaling is computed as:

- `real_capacity_2025_effective = col_2010 + col_2015 + col_2020 + col_2025`

Note:

- The `2025` column in `solar capacity.csv` has been adjusted to represent incremental 2025 addition (`2025_cumulative - previous_existing`).
- Therefore the sum above gives the intended cumulative 2025 real capacity.

For each province:

- `capacity_ratio = real_capacity_2025_effective / nc_solar_capacity`

This ratio is used to correct solar generation for penetration calculation:

- `solar_generation_for_penetration = nc_solar_generation * capacity_ratio`

## 5) Metric Definitions

## 5.1 `solar_ele_GWh`

- Sum provincial solar dispatch over all 2025 snapshots, converted from MWh to GWh.

## 5.2 `value_factor` (standard formula)

The current implementation follows:

`value_factor = (PV weighted average price) / (system weighted average price)`

Where:

- PV weighted average price:
  - `sum_t(PV_t * Price_t) / sum_t(PV_t)`
- System weighted average price:
  - `sum_t(TotalGen_t * Price_t) / sum_t(TotalGen_t)`

Written workbook fields:

- `value_factor_numerator` = PV weighted average price
- `value_factor_denominator` = system weighted average price
- `value_factor` = numerator / denominator

## 5.3 `solar_penetration`

Two penetration ratios are computed using corrected solar generation:

- Load-based ratio:
  - `penetration_load = solar_generation_for_penetration / provincial_total_load`
- Generation-based ratio:
  - `penetration_gen = solar_generation_for_penetration / provincial_total_generation`

Final conservative value:

- `solar_penetration = min(penetration_load, penetration_gen)`

## 5.4 `solar_curtailment_rate`

- `solar_curtailment_rate = (available_solar - dispatched_solar) / available_solar`
- Available solar is derived from:
  - `p_max_pu * p_nom_opt` (summed over solar generators)

## 5.5 `solar_capacity_factor`

- `solar_capacity_factor = dispatched_solar / (nc_solar_capacity * total_weighted_hours)`

## 6) Validation Outputs

The script also exports capacity comparison for checking:

- `results/version-0505.1H.2/solar_capacity_compare_2025.csv`

Columns include:

- `nc_solar_capacity_mw`
- `real_solar_capacity_mw`
- `real_to_nc_ratio`

## 7) Re-run Command

Use:

```bash
conda run -n pypsa python scripts/fill_solar_value_dataset_2025.py
```
