# SPDX-FileCopyrightText: : 2026 Ruike Lyu
#
# SPDX-License-Identifier: MIT

"""
Optimization Snakefile (full electricity+heat model).

This workflow produces solved `postnetwork-*.nc` files via `solve_network_myopic.py`.

Why this file exists:
- The repo's default `Snakefile` is currently a minimal heat-only workflow.
- Some analyses (and any endogenous electricity prices) require the full optimized
  network results (`postnetworks/`).

Run:
  conda run -n pypsa snakemake -s Snakefile_optimize --cores 6
"""

from os.path import normpath

configfile: "config.yaml"


if config["foresight"] == "myopic":

    rule all:
        input:
            expand(
                config["results_dir"]
                + "version-"
                + str(config["version"])
                + "/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc",
                **config["scenario"],
            )

    # Baseyear prenetwork (first planning horizon).
    # Historically hardcoded to 2020; generalized to the configured baseyear.
    rule prepare_base_networks_2020:
        input:
            config="config.yaml",
            overrides="data/override_component_attrs",
            edges="data/grids/edges.txt",
            edges_ext="data/grids/edges_current.csv",
            solar_thermal_name="data/heating/solar_thermal-{angle}.h5".format(angle=config["solar_thermal_angle"]),
            cop_name="data/heating/cop.h5",
            province_shape="data/province_shapes/CHN_adm1.shp",
            elec_load="data/load/load_{planning_horizons}_weatheryears_1979_2016_TWh.h5",
            aluminum_load="data/load/load_{planning_horizons}_weatheryears_1979_2016_TWh.h5",
            al_smelter_p_max="data/p_nom/al_smelter_p_max.csv",
            aluminum_demand_json="data/aluminum_demand/aluminum_demand_all_scenarios.json",
            heat_demand_profile="data/heating/heat_demand_profile_{heating_demand}_{planning_horizons}.h5",
            central_fraction="data/heating/DH_city_town_2020.h5",
            tech_costs="data/costs/costs_{planning_horizons}.csv",
            biomass_potental="data/p_nom/biomass_potential.h5",
            **{f"profile_{tech}": f"resources/profile_{tech}.nc" for tech in config["renewable"]},
        output:
            network_name=config["results_dir"]
            + "version-"
            + str(config["version"])
            + "/prenetworks/{heating_demand}/prenetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc",
        wildcard_constraints:
            planning_horizons=config["scenario"]["planning_horizons"][0],  # only applies to baseyear
        threads: config["threads"]
        resources: mem_mb=config["mem_per_thread"] * config["threads"]
        script: "scripts/prepare_base_network.py"

    rule prepare_base_networks:
        input:
            overrides="data/override_component_attrs",
            edges="data/grids/edges.txt",
            solar_thermal_name="data/heating/solar_thermal-{angle}.h5".format(angle=config["solar_thermal_angle"]),
            cop_name="data/heating/cop.h5",
            province_shape="data/province_shapes/CHN_adm1.shp",
            elec_load="data/load/load_{planning_horizons}_weatheryears_1979_2016_TWh.h5",
            aluminum_load="data/load/load_{planning_horizons}_weatheryears_1979_2016_TWh.h5",
            al_smelter_p_max="data/p_nom/al_smelter_p_max.csv",
            aluminum_demand_json="data/aluminum_demand/aluminum_demand_all_scenarios.json",
            heat_demand_profile="data/heating/heat_demand_profile_{heating_demand}_{planning_horizons}.h5",
            central_fraction="data/heating/DH_city_town_2020.h5",
            tech_costs="data/costs/costs_{planning_horizons}.csv",
            biomass_potental="data/p_nom/biomass_potential.h5",
            **{f"profile_{tech}": f"resources/profile_{tech}.nc" for tech in config["renewable"]},
        output:
            network_name=config["results_dir"]
            + "version-"
            + str(config["version"])
            + "/prenetworks/{heating_demand}/prenetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc",
        threads: config["threads"]
        resources: mem_mb=config["mem_per_thread"] * config["threads"]
        script: "scripts/prepare_base_network.py"

    ruleorder: prepare_base_networks_2020 > prepare_base_networks

    rule add_existing_baseyear:
        input:
            overrides="data/override_component_attrs",
            network=config["results_dir"]
            + "version-"
            + str(config["version"])
            + "/prenetworks/{heating_demand}/prenetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc",
            tech_costs="data/costs/costs_{planning_horizons}.csv",
            cop_name="data/heating/cop.h5",
            **{f"existing_{tech}": f"data/existing_infrastructure/{tech} capacity.csv" for tech in config["existing_infrastructure"]},
        output:
            config["results_dir"]
            + "version-"
            + str(config["version"])
            + "/prenetworks-brownfield/{heating_demand}/prenetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc"
        wildcard_constraints:
            planning_horizons=config["scenario"]["planning_horizons"][0],  # only applies to baseyear
        threads: config["threads"]
        resources: mem_mb=config["mem_per_thread"] * config["threads"]
        script: "scripts/add_existing_baseyear.py"

    def solved_previous_horizon(wildcards):
        planning_horizons = config["scenario"]["planning_horizons"]
        i = planning_horizons.index(int(wildcards.planning_horizons))
        planning_horizon_p = str(planning_horizons[i - 1])
        return (
            config["results_dir"]
            + "version-"
            + str(config["version"])
            + "/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-"
            + planning_horizon_p
            + ".nc"
        )

    rule add_brownfield:
        input:
            overrides="data/override_component_attrs",
            network=config["results_dir"]
            + "version-"
            + str(config["version"])
            + "/prenetworks/{heating_demand}/prenetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc",
            network_p=solved_previous_horizon,
            costs="data/costs/costs_{planning_horizons}.csv",
            **{f"profile_{tech}": f"resources/profile_{tech}.nc" for tech in config["renewable"]},
        output:
            network_name=config["results_dir"]
            + "version-"
            + str(config["version"])
            + "/prenetworks-brownfield/{heating_demand}/prenetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc",
        threads: config["threads"]
        resources: mem_mb=config["mem_per_thread"] * config["threads"]
        script: "scripts/add_brownfield.py"

    ruleorder: add_existing_baseyear > add_brownfield

    rule solve_network_myopic:
        params:
            solving=config["solving"],
            planning_horizons=config["scenario"]["planning_horizons"],
            using_single_node=config["using_single_node"],
            single_node_province=config["single_node_province"],
            iterative_optimization=config["iterative_optimization"],
        input:
            overrides="data/override_component_attrs",
            network=config["results_dir"]
            + "version-"
            + str(config["version"])
            + "/prenetworks-brownfield/{heating_demand}/prenetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc",
            costs="data/costs/costs_{planning_horizons}.csv",
            biomass_potental="data/p_nom/biomass_potential.h5",
            al_smelter_p_max="data/p_nom/al_smelter_p_max.csv",
        output:
            network_name=config["results_dir"]
            + "version-"
            + str(config["version"])
            + "/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc"
        log:
            solver=normpath(
                "logs/solve_operations_network/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.log"
            )
        threads: config["threads"]
        resources: mem_mb=config["mem_per_thread"] * config["threads"]
        script: "scripts/solve_network_myopic.py"

    ruleorder: prepare_base_networks > add_existing_baseyear > solve_network_myopic

