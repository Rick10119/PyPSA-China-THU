# rule plot_network:
#     input:
#         network=config['results_dir'] + 'version-' + str(
#             config['version']) + '/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc',
#     output:
#         cost_map=config['results_dir'] + 'version-' + str(
#             config['version']) + '/plots/network/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}-cost.pdf',
#     log: "logs/plot_network/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.log"
#     script: "../scripts/plot_network.py"

# rule plot_network_heat:
#     """
#     Create comprehensive network heat map visualizations including:
#     - Geographic map of transmission lines and power plants
#     - Energy mix pie chart
#     - Cost breakdown bar chart
#     """
#     input:
#         network=config['results_dir'] + 'version-' + str(
#             config['version']) + '/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc',
#         tech_costs="data/costs/costs_{planning_horizons}.csv"
#     output:
#         only_map=config['results_dir'] + 'version-' + str(
#             config['version']) + '/plots/network/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}_map_only.pdf',
#         ext=config['results_dir'] + 'version-' + str(
#             config['version']) + '/plots/network/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}_ext_heat.pdf'
#     log: "logs/plot_network_heat/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.log"
#     resources: mem_mb=4000
#     script: "../scripts/plot_network_heat.py"

rule make_summary:
    input:
        network=config['results_dir'] + 'version-' + str(
            config['version']) + '/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc',
    output:
        directory(config['results_dir'] + 'version-' + str(
            config['version']) + '/summary/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}'),
        costs=config['results_dir'] + 'version-' + str(
            config['version']) + '/summary/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}/costs.csv'
    log: "logs/make_summary/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.log"
    resources: mem_mb=config['mem_per_thread'] * config['threads']
    script: "../scripts/make_summary.py"

# ruleorder: solve_all_networks > make_summary

rule plot_summary:
    input:
        config['results_dir'] + 'version-' + str(
            config['version']) + '/summary/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}'
    output:
        energy=config['results_dir'] + 'version-' + str(
            config['version']) + '/plots/summary/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.png',
        cost=config['results_dir'] + 'version-' + str(
            config['version']) + '/plots/summary/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}_costs.png'
    log: "logs/plot/summary/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.log"
    script: "../scripts/plot_summary.py"

rule plot_heatmap:
    input:
        network = config['results_dir'] + 'version-' + str(config['version']) + '/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc',
    output:
        water = config['results_dir'] + 'version-' + str(config['version']) + '/plots/heatmap/{heating_demand}/water_tank/water_tank-{opts}-{topology}-{pathway}-{planning_horizons}.png',
        water_store = config['results_dir'] + 'version-' + str(config['version']) + '/plots/heatmap/{heating_demand}/water_tank/water_store-{opts}-{topology}-{pathway}-{planning_horizons}.png',
        battery = config['results_dir'] + 'version-' + str(config['version']) + '/plots/heatmap/{heating_demand}/battery/battery-{opts}-{topology}-{pathway}-{planning_horizons}.png',
        H2 = config['results_dir'] + 'version-' + str(config['version']) + '/plots/heatmap/{heating_demand}/H2/H2-{opts}-{topology}-{pathway}-{planning_horizons}.png',
        # aluminum = config['results_dir'] + 'version-' + str(config['version']) + '/plots/heatmap/{heating_demand}/aluminum/aluminum-{opts}-{topology}-{pathway}-{planning_horizons}.png',
    script:  "../scripts/plot_heatmap.py"

# rule plot_profile:
#     input:
#         network = config['results_dir'] + 'version-' + str(config['version']) + '/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc',
#     output:
#         weekly_operation_heating = config['results_dir'] + 'version-' + str(config['version']) + '/plots/weekly_operation/{heating_demand}/weekly_operation_heating-{opts}-{topology}-{pathway}-{planning_horizons}.png',
#         weekly_operation_non_heating = config['results_dir'] + 'version-' + str(config['version']) + '/plots/weekly_operation/{heating_demand}/weekly_operation_non_heating-{opts}-{topology}-{pathway}-{planning_horizons}.png',
#         heating_comparison = config['results_dir'] + 'version-' + str(config['version']) + '/plots/heating_comparison/{heating_demand}/heating_comparison-{opts}-{topology}-{pathway}-{planning_horizons}.png',
#     script:  "../scripts/plot_profile.py"

rule plot_capacity_factors:
    """
    Generate capacity factor plots for different energy resources showing monthly variations.
    """
    input:
        network = config['results_dir'] + 'version-' + str(config['version']) + '/postnetworks/{heating_demand}/postnetwork-{opts}-{topology}-{pathway}-{planning_horizons}.nc',
    output:
        capacity_factors = config['results_dir'] + 'version-' + str(config['version']) + '/plots/capacity_factors/{heating_demand}/capacity_factors-{opts}-{topology}-{pathway}-{planning_horizons}.png',
    script:  "../scripts/plot_capacity_factors.py"