#!/usr/bin/env python3
# SPDX-FileCopyrightText: : 2025 Ruike Lyu, rl8728@princeton.edu
"""
Advanced SLURM job-file generator for PyPSA-China.

This script automatically discovers scenario configuration files and generates
corresponding SLURM job scripts for batch execution on HPC systems.
"""

import os
import yaml
import glob
from pathlib import Path
from typing import List, Dict, Any

class SlurmJobGenerator:
    """Helper class for generating SLURM job scripts."""
    
    def __init__(self, base_config="config.yaml", output_dir="jobs"):
        """
        Initialize the generator.

        Args:
            base_config (str): Path to the base configuration file.
            output_dir (str): Directory in which SLURM job files are written.
        """
        self.base_config = base_config
        self.output_dir = Path(output_dir)
        
        # Clean up any existing `.slurm` files in the jobs folder
        if self.output_dir.exists():
            # Delete all existing `.slurm` files
            for file_path in self.output_dir.glob('*.slurm'):
                if file_path.is_file():
                    file_path.unlink()
                    print(f"Removed old SLURM file: {file_path}")
            print("Existing SLURM files in the jobs folder have been cleared.")
        else:
            self.output_dir.mkdir(exist_ok=True)
            print("Created jobs folder.")
        
        self.scenarios = []
        
    def discover_scenarios(self):
        """Automatically discover all available scenario configuration files."""
        print("Discovering scenario configuration files...")
        
        # Look for all `config_*.yaml` files under the `configs` directory
        configs_dir = Path("configs")
        if not configs_dir.exists():
            print("  ✗ `configs` directory not found. Run `generate_capacity_configs.py` first to create scenario configs.")
            return []
        
        config_files = list(configs_dir.glob("config_*.yaml"))
        config_files.sort()
        
        discovered_scenarios = []
        
        for config_file in config_files:
            # Derive the scenario name from the filename
            if config_file.name == "config.yaml":
                continue
                
            scenario_name = config_file.stem.replace("config_", "")
            
            # Read the configuration file to obtain additional metadata
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config_data = yaml.safe_load(f)
                
                # Extract scenario information
                version = config_data.get('version', 'unknown')
                description = self._generate_description(scenario_name, config_data)
                
                scenario_info = {
                    "name": scenario_name,
                    "config_file": str(config_file),  # Use the full path
                    "description": description,
                    "version": version,
                    "config_data": config_data
                }
                
                discovered_scenarios.append(scenario_info)
                print(f"  ✓ Found scenario: {scenario_name} -> {description}")
                
            except Exception as e:
                print(f"  ✗ Failed to read config file {config_file}: {e}")
        
        self.scenarios = discovered_scenarios
        print(f"Discovered {len(discovered_scenarios)} scenario(s).")
        print()
        
        return discovered_scenarios
    
    def _generate_description(self, scenario_name: str, config_data: Dict[str, Any]) -> str:
        """Generate a short English description based on the scenario name and config."""
        
        # New naming convention examples:
        #   config_LMM_2030_100p.yaml
        #   config_LMM_2030_no_aluminum.yaml
        #   config_LMM_2050_non_flexible.yaml
        if '_' in scenario_name:
            parts = scenario_name.split('_')
            if len(parts) >= 3:
                # First part encodes flexibility + demand + market (e.g. LMM)
                scenario_code = parts[0]
                # Second part is the model year (e.g. 2030, 2050)
                year = parts[1]
                # Third part and beyond encode the capacity type (e.g. 100p, no_aluminum, non_flexible)
                capacity_part = '_'.join(parts[2:])
                
                # Decode the 3-letter scenario code
                flex_map = {'L': 'low', 'M': 'mid', 'H': 'high', 'N': 'non_constrained'}
                if len(scenario_code) == 3:
                    flex = flex_map.get(scenario_code[0], 'unknown')
                    demand = flex_map.get(scenario_code[1], 'unknown')
                    market = flex_map.get(scenario_code[2], 'unknown')
                    
                    # Interpret the capacity-type suffix
                    if capacity_part == 'no_aluminum':
                        return f"No-aluminum baseline scenario (Flexibility: {flex}, Demand: {demand}, Market: {market}, Year: {year})"
                    elif capacity_part == 'non_flexible':
                        return f"Non-flexible baseline group (Flexibility: {flex}, Demand: {demand}, Market: {market}, Year: {year})"
                    elif capacity_part.endswith('p'):
                        try:
                            percentage = int(capacity_part.replace('p', ''))
                            return f"{percentage}% aluminum capacity ratio (Flexibility: {flex}, Demand: {demand}, Market: {market}, Year: {year})"
                        except ValueError:
                            pass
                    else:
                        return f"{capacity_part} configuration (Flexibility: {flex}, Demand: {demand}, Market: {market}, Year: {year})"
        
        # Backwards compatibility for older naming conventions
        if scenario_name == "no_aluminum":
            return "Baseline scenario without aluminum smelters"
        
        # Check simple capacity-ratio scenarios of the form '100p', '60p', etc.
        if scenario_name.endswith('p'):
            try:
                percentage = int(scenario_name.replace('p', ''))
                return f"{percentage}% aluminum capacity ratio"
            except ValueError:
                pass
        
        # Inspect config data for explicit `aluminum_capacity_ratio` overrides
        if 'aluminum_capacity_ratio' in config_data:
            ratio = config_data['aluminum_capacity_ratio']
            if isinstance(ratio, (int, float)):
                percentage = int(ratio * 100)
                return f"{percentage}% aluminum capacity ratio"
        
        if config_data.get('add_aluminum') == False:
            return "Scenario without aluminum smelters"
        
        # Fallback description
        return f"Scenario: {scenario_name}"
    
    def generate_slurm_job(self, scenario: Dict[str, Any], 
                          template_params: Dict[str, Any] = None) -> str:
        """
        Generate a single SLURM job script for a given scenario.

        Args:
            scenario (Dict): Scenario metadata dictionary.
            template_params (Dict): Optional overrides for job resources.

        Returns:
            str: Generated job-file name.
        """
        
        # Default resource and module settings for the template
        default_params = {
            "nodes": 1,
            "ntasks": 1,
            "cpus_per_task": 32,
            "mem_per_cpu": "20G",
            "time_limit": "71:59:00",
            "mail_user": "rl8728@princeton.edu",
            "modules": [
                "module purge",
                "module load anaconda3/2024.10",
                "conda activate pypsa-china",
                "module load gurobi/12.0.0"
            ]
        }
        
        # Merge user-specified parameters (if any)
        if template_params:
            default_params.update(template_params)
        
        # Render the final SLURM script content as a string
        slurm_content = self._generate_slurm_content(scenario, default_params)
        
        # Write script to disk
        job_filename = f"job_{scenario['name']}.slurm"
        job_path = self.output_dir / job_filename
        
        with open(job_path, 'w', encoding='utf-8') as f:
            f.write(slurm_content)
        
        # Mark the script as executable
        os.chmod(job_path, 0o755)
        
        print(f"✓ Generated: {self.output_dir}/{job_filename}")
        return job_filename
    
    def _generate_slurm_content(self, scenario: Dict[str, Any], 
                               params: Dict[str, Any]) -> str:
        """Create the text body of a SLURM job script."""
        
        # Build the module-loading command block
        module_commands = "\n".join(params["modules"])
        
        slurm_content = f"""#!/bin/bash
#SBATCH --job-name=pypsa-china-{scenario['name']}        # Job name
#SBATCH --nodes={params['nodes']}                # Number of nodes
#SBATCH --ntasks={params['ntasks']}               # Total number of tasks
#SBATCH --cpus-per-task={params['cpus_per_task']}       # CPU cores per task
#SBATCH --mem-per-cpu={params['mem_per_cpu']}        # Memory per CPU core
#SBATCH --time={params['time_limit']}          # Wall-clock time limit
#SBATCH --mail-type=fail         # Send email on failure only
#SBATCH --mail-user={params['mail_user']}

# Configure log file
LOG_FILE="logs/job_{scenario['name']}_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=== PyPSA-China job for {scenario['description']} started ==="
echo "Start time: $(date)"
echo "Job ID: $SLURM_JOB_ID"
echo "Nodes: $SLURM_NODELIST"
echo "Log file: $LOG_FILE"
echo "Version tag: {scenario['version']}"
echo

# Load required modules and activate the environment
echo "Loading modules..."
{module_commands}

echo "Modules loaded."
echo

# Run the PyPSA-China workflow
echo "Starting simulation for {scenario['description']} ..."
echo "Config file: {scenario['config_file']}"
echo "Start time: $(date)"
echo

START_TIME=$(date +%s)

# When FORCE_RESTART=1, re-run the full workflow to avoid stale outputs.
FORCE_RESTART="${{FORCE_RESTART:-0}}"
SNAKEMAKE_EXTRA_ARGS=""
if [ "$FORCE_RESTART" = "1" ]; then
    echo "FORCE_RESTART=1: using --forceall --rerun-incomplete to restart from scratch."
    SNAKEMAKE_EXTRA_ARGS="--forceall --rerun-incomplete"
fi

if snakemake --configfile {scenario['config_file']} --cores {params['cpus_per_task']} $SNAKEMAKE_EXTRA_ARGS; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    echo "✓ Simulation for {scenario['description']} completed successfully."
    echo "Runtime: $((DURATION / 3600)) h $((DURATION % 3600 / 60)) min $((DURATION % 60)) s"
else
    echo "✗ Simulation for {scenario['description']} failed."
    exit 1
fi

echo
echo "=== PyPSA-China job for {scenario['description']} finished ==="
echo "End time: $(date)"
echo "Log file: $LOG_FILE"
"""
        
        return slurm_content
    
    def generate_all_jobs(self, template_params: Dict[str, Any] = None) -> List[str]:
        """
        Generate SLURM job scripts for all discovered scenarios.

        Args:
            template_params (Dict): Optional overrides for job resources.

        Returns:
            List[str]: List of generated job-file names.
        """
        
        if not self.scenarios:
            self.discover_scenarios()
        
        print("Generating SLURM job scripts for all scenarios...")
        print()
        
        generated_files = []
        
        for scenario in self.scenarios:
            filename = self.generate_slurm_job(scenario, template_params)
            generated_files.append(filename)
        
        print()
        print("=== Job generation complete ===")
        print(f"Generated {len(generated_files)} SLURM job file(s):")
        for filename in generated_files:
            print(f"  - {filename}")
        
        return generated_files
    
    def generate_custom_job(self, scenario_name: str, config_file: str, 
                           description: str, **kwargs) -> str:
        """
        Generate a SLURM job file for an arbitrary, user-defined scenario.

        Args:
            scenario_name (str): Scenario name used in the job ID and filename.
            config_file (str): Path to the scenario configuration file.
            description (str): Human-readable scenario description.
            **kwargs: Additional template overrides (e.g. `cpus_per_task`, `time_limit`).

        Returns:
            str: Generated job-file name.
        """
        
        custom_scenario = {
            "name": scenario_name,
            "config_file": config_file,
            "description": description,
            "version": "custom",
            "config_data": {}
        }
        
        return self.generate_slurm_job(custom_scenario, kwargs)

def main():
    """Entry point for command-line usage."""
    print("PyPSA-China advanced SLURM job generator")
    print("=" * 60)
    print()
    
    # Check that we are in the project root (where `config.yaml` lives)
    if not os.path.exists("config.yaml"):
        print("Warning: `config.yaml` not found. Please run this script from the PyPSA-China project root.")
        print()
    
    # Create generator instance
    generator = SlurmJobGenerator()
    
    # Discover scenarios
    scenarios = generator.discover_scenarios()
    
    if not scenarios:
        print("No scenario configuration files found. Run `generate_capacity_configs.py` first.")
        return
    
    # Generate job scripts for all scenarios
    generated_files = generator.generate_all_jobs()
    
    print()
    print("Usage examples:")
    print("1. Submit a single job:   sbatch jobs/job_100p.slurm")
    print("2. Submit all jobs:       ./submit_multiple_jobs.sh")
    print("3. Submit capacity jobs:  ./submit_capacity_jobs.sh")
    
    print()
    print("Advanced usage example:")
    print("# Generate a custom job file with modified resources")
    print("generator = SlurmJobGenerator()")
    print("generator.generate_custom_job('test', 'config_test.yaml', 'Test scenario', cpus_per_task=32, time_limit='6:00:00')")
    
    return generated_files

if __name__ == "__main__":
    main() 