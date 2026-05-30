$ErrorActionPreference = "Stop"

$ConfigPath = if ($env:CONFIG_PATH) { $env:CONFIG_PATH } else { "config.yaml" }
$ExtraArgs = if ($env:FORCE_RESTART -eq "1") { @("--forceall", "--rerun-incomplete") } else { @() }

snakemake --configfile $ConfigPath --cores 18 --resources mem_mb=112000 @ExtraArgs
