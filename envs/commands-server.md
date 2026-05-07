# Server Commands (PyPSA-China)

## 1) Environment setup (first run or env update)

```bash
module load anaconda3/2024.6
conda env update -f envs/environment.yaml
conda activate pypsa
python -c "import pypsa; print('pypsa', pypsa.__version__)"
```

## 2) Copy code to server directories

```bash
mv ~/Documents/PyPSA-China /scratch/gpfs/rl8728/PyPSA-China
cp -R ~/Documents/PyPSA-China /scratch/gpfs/rl8728/PyPSA-China
```

## 3) Scenario test (JENKINS path)

```bash
cd /scratch/gpfs/JENKINS/rl8728/PyPSA-China
module load anaconda3/2024.6
conda activate pypsa

git fetch --all --prune
git checkout "price-simulation"
git pull
snakemake --unlock
snakemake -np
sbatch job.slurm

chmod +x submit_multiple_jobs.sh
./submit_multiple_jobs.sh

chmod +x submit_core_scenario.sh
./submit_core_scenario.sh
```

## 4) Run production jobs (example: Sep 2)

```bash
cd /scratch/gpfs/rl8728/PyPSA-China
module load anaconda3/2024.6
conda activate pypsa

git restore .
git pull
find ./results | xargs touch
snakemake --unlock

chmod +x submit_multiple_jobs.sh
./submit_multiple_jobs.sh
```

## 5) Common ops commands

### Cancel all jobs

```bash
scancel -u rl8728
```

### Refresh result file timestamps

```bash
find ./results | xargs touch
```

## 6) Plotting / debugging (example: Sep 2)

```bash
find ./results/version-0506.1H.1 | xargs touch 
snakemake --unlock
snakemake --configfile configs/config_MMMF_2050_10p.yaml -np --rerun-incomplete --ignore-incomplete --rerun-triggers mtime
snakemake --configfile configs/config_MMMF_2050_10p.yaml --cores 6 --rerun-incomplete --ignore-incomplete --rerun-triggers mtime
```
