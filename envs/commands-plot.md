cd /scratch/gpfs/JENKINS/rl8728/PyPSA-China
module load anaconda3/2024.6
conda activate pypsa

git restore .
git pull
# snakemake --unlock

sbatch jobs_plot/job_plot_value_scenario_comparison_f.slurm
sbatch jobs_plot/job_plot_optimal_point.slurm
sbatch jobs_plot/job_plot_capacity.slurm
sbatch jobs_plot/job_plot_value_scenario_comparison.slurm
sbatch jobs_plot/job_plot_optimal_point.slurm
sbatch jobs_plot/job_plot_capacity_factor.slurm
```
