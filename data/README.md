# Data Directory

This directory contains all input data files required for running PyPSA-China energy system optimization model. The data is organized into subdirectories by category.

## Directory Structure

### `aluminum_demand/`
Contains aluminum demand scenarios for different planning horizons.
- **`aluminum_demand_all_scenarios.json`**: JSON file containing aluminum demand data for all scenarios

### `costs/`
Technology cost data files organized by planning horizon year.
- **`costs_YYYY.csv`**: Technology cost data for years 2020, 2025, 2030, 2035, 2040, 2045, 2050, 2055, 2060
- Each file contains cost parameters (investment, FOM, VOM, etc.) for different energy technologies

### `costs_constant/` (removed)
This repo previously contained `data/costs_constant/` as an alternative constant-cost dataset.
It has been removed to avoid duplicated/ambiguous cost inputs. The workflow reads
`data/costs/costs_{planning_horizons}.csv` (see `Snakefile`).

### `existing_infrastructure/`
Data on existing power generation infrastructure in China.
- **`China_current_capacity.*`**: Shapefile and CSV files containing current installed capacity by province
- **`*_capacity.csv`**: Capacity data for specific technologies:
  - `coal capacity.csv`: Coal power plants
  - `CHP coal capacity.csv`: Combined Heat and Power (coal)
  - `CHP gas capacity.csv`: Combined Heat and Power (gas)
  - `coal boiler capacity.csv`: Coal boilers
  - `nuclear capacity.csv`: Nuclear power plants
  - `hydroelectricity capacity.csv`: Hydroelectric power
  - `onwind capacity.csv`: Onshore wind
  - `offwind capacity.csv`: Offshore wind
  - `solar capacity.csv`: Solar PV
  - `solar thermal capacity.csv`: Solar thermal
  - `ground heat pump capacity.csv`: Ground source heat pumps
  - `OCGT capacity.csv`: Open Cycle Gas Turbines
  - `decentral coal boiler percentrage.csv`: Decentralized coal boiler percentages
- **`Global-*-Tracker-*.xlsx`**: Excel files from Global Energy Monitor tracking global power plant data

### `grids/`
Transmission network topology and connectivity data.
- **`edges.txt`**: Main transmission line edges file (used in network preparation)
- **`edges_current.csv`**: Current transmission network edges
- **`edges_current_neighbor.csv`**: Transmission edges including neighboring connections
- **`old/`**: Archive of older grid topology files

### `heating/`
Heating demand and supply data for district heating systems.
- **`heat_demand_profile_{scenario}_{year}.h5`**: Hourly heating demand profiles by scenario and year
- **`solar_thermal-{angle}.h5`**: Solar thermal generation profiles at different tilt angles
- **`cop.h5`**: Coefficient of Performance (COP) data for heat pumps
- **`DH_city:town_YYYY.h5`**: District heating central fraction data by city/town type
- Multiple HDF5 files containing time-series heating data

### `hydro/`
Hydroelectric power generation data including reservoirs and pumped hydro storage.
- **`Capacities_2014_MWh.pickle`**: Hydro capacity data from 2014
- **`daily_hydro_inflow_per_dam_1979_2016_GWh.pickle`**: Daily inflow data per dam (1979-2016) in GWh
- **`daily_hydro_inflow_per_dam_1979_2016_m3.pickle`**: Daily inflow data per dam (1979-2016) in cubic meters
- **`dams_large.csv`**: Large dam locations and characteristics
- **`installed_hydro_capacity_2015_MW.pickle`**: Installed hydro capacity in 2015
- **`PHS_p_nom.csv`**: Pumped Hydro Storage (PHS) nominal power capacity
- **`reservoir_*.pickle`**: Reservoir capacity data (total, effective, initial)

### `landuse_availability/`
Land use constraints and availability for renewable energy deployment.
- Contains data on land availability for solar and wind installations

### `load/`
Electricity demand (load) profiles by province and year.
- **`load_{year}_weatheryears_1979_2016_TWh.h5`**: Hourly load profiles for years 2020-2060, covering weather years 1979-2016
- **`Hourly_demand_of_31_province_China_modified - V2.1.csv`**: Hourly demand data for 31 provinces
- **`Province_Load_2020_2060.csv`**: Province-level load projections 2020-2060

### `override_component_attrs/`
Component attribute overrides for buses and links.
- **`buses.csv`**: Custom bus attributes
- **`links.csv`**: Custom link attributes
- Used to modify default PyPSA component properties

### `p_nom/`
Nominal power capacity and potential data for various technologies.
- **`al_smelter_p_max.csv`**: Maximum power capacity for aluminum smelters
- **`al_production_ratio.csv`**: Aluminum production ratios
- **`biomass_potential.h5`**: Biomass resource potential
- **`hydro_p_nom.csv`** / **`hydro_p_nom.h5`**: Hydro nominal power capacity
- **`hydro_p_max_pu.h5`**: Hydro maximum power per unit
- **`nuclear_p_nom.csv`** / **`nuclear_p_nom.h5`**: Nuclear nominal power capacity
- **`p_nom_max_cc.csv`**: Maximum nominal power for combined cycle plants
- **`41467_2021_23282_MOESM4_ESM.xlsx`**: Supplementary data file

### `population/`
Population distribution data for demand allocation.
- **`population.h5`**: Gridded population data
- **`population_gridcell_map.h5`**: Mapping between grid cells and population
- **`population_from_National_Data_2020.csv`**: Population data from national statistics 2020
- **`CFSR_grid.nc`**: Climate Forecast System Reanalysis (CFSR) grid data

### `province_shapes/`
Geographic shapefiles for Chinese administrative boundaries.
- **`CHN_adm1.shp`**: Main province-level administrative boundaries (used in network preparation)
- **`CHN_adm/`**: Full administrative boundary dataset
- **`chn_adm_ocha_2020_shp/`**: UN OCHA administrative boundaries (2020)
- **`CHN_full_adm/`**: Complete administrative boundaries
- Contains shapefile components (.shp, .shx, .dbf, .prj, .cpg, etc.)

### `resources/`
Renewable energy resource potential and geographic regions.
- **`regions_onshore.geojson`**: Onshore wind/solar resource regions
- **`regions_offshore.geojson`**: Offshore wind resource regions
- **`regions_offshore_province.geojson`**: Offshore regions mapped to provinces
- Note: Renewable generation profiles (e.g., `profile_{tech}.nc`) are stored in the `resources/` directory at the project root, not in `data/resources/`

## Data File Formats

### CSV Files
- Standard comma-separated values format
- Used for tabular data (costs, capacities, load summaries)
- Encoding: UTF-8

### HDF5 Files (.h5)
- Hierarchical Data Format version 5
- Used for large time-series data (load profiles, heating demand, renewable profiles)
- Efficient storage for multi-dimensional arrays
- Can be read using `pandas.read_hdf()` or `h5py`

### Pickle Files (.pickle)
- Python serialization format
- Used for storing Python objects (arrays, dictionaries)
- Load using `pickle.load()` or `pandas.read_pickle()`

### Shapefiles (.shp, .shx, .dbf, .prj)
- Geographic vector data format
- Used for administrative boundaries and spatial data
- Read using `geopandas` or `fiona`

### GeoJSON Files (.geojson)
- JSON-based geographic data format
- Used for renewable resource regions
- Read using `geopandas.read_file()`

### NetCDF Files (.nc)
- Network Common Data Form
- Used for gridded climate and weather data
- Read using `xarray` or `netCDF4`

## Data Usage in Workflow

The data files are referenced in the Snakemake workflow (`Snakefile`) during network preparation:

1. **Base Network Preparation** (`prepare_base_networks`):
   - Loads grid topology from `grids/edges.txt`
   - Loads province shapes from `province_shapes/CHN_adm1.shp`
   - Loads load profiles from `load/load_{year}_weatheryears_1979_2016_TWh.h5`
   - Loads technology costs from `costs/costs_{year}.csv`
   - Loads heating data from `heating/` directory
   - Loads renewable profiles from `resources/profile_{tech}.nc` (project root)

2. **Existing Infrastructure** (`add_existing_baseyear`):
   - Loads existing capacity data from `existing_infrastructure/*_capacity.csv`

3. **Aluminum Integration**:
   - Loads aluminum demand from `aluminum_demand/aluminum_demand_all_scenarios.json`
   - Loads smelter capacity from `p_nom/al_smelter_p_max.csv`

## Data Sources

- **Power Plant Data**: Global Energy Monitor (Global Coal Plant Tracker, Global Gas Plant Tracker, Global Solar Power Tracker, Global Wind Power Tracker)
- **Load Data**: National and provincial electricity demand statistics
- **Cost Data**: Technology cost projections from various sources
- **Geographic Data**: Administrative boundaries from various sources (GADM, UN OCHA)
- **Renewable Resources**: Derived from reanalysis data and resource assessments
- **Population Data**: National statistical data

## Data Updates

When updating data files:
1. Ensure file naming conventions match the patterns expected by the workflow
2. Verify data formats (CSV encoding, HDF5 structure, etc.)
3. Update corresponding documentation if data structure changes
4. Test that the workflow can successfully load the updated data

## Notes

- Some large data files may be excluded from version control (check `.gitignore`)
- Data files are typically read-only during model execution
- Time-series data covers multiple weather years (1979-2016) for robust optimization
- Cost data is organized by planning horizon year for scenario analysis

