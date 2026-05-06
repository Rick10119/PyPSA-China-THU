# SPDX-FileCopyrightText: : 2026 Ruike Lyu
# SPDX-License-Identifier: MIT
"""Overwrite solar capacity.csv '2025' column from NEA bulletin cumulative totals.

 bulletin: https://www.nea.gov.cn/20251112/35126d06a151461882b61d0a2e5706a6/c.html
 Uses 截至2025年9月底累计并网容量 (合计, 万千瓦) per province.

 PyPSA / readme_cn convention: Δ_2025 = max(C_nea_sep2025_mw − sum(2010..2020)_mw, 0).

 Run: conda run -n pypsa python3 scripts/recompute_solar_2025_from_nea_sep2025.py
"""

from __future__ import annotations

import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSV = ROOT / "data/existing_infrastructure/solar capacity.csv"

# province -> 截至2025年9月底累计并网 (万千瓦), 合计
NEA_万kW = {
    "Beijing": 192.5,
    "Tianjin": 987.6,
    "Hebei": 8152.1,
    "Shanxi": 4834.6,
    "InnerMongolia": 5090.7,
    "Liaoning": 1511.6,
    "Jilin": 701.2,
    "Heilongjiang": 863.5,
    "Shanghai": 571.4,
    "Jiangsu": 8611.5,
    "Zhejiang": 6105.6,
    "Anhui": 5438.1,
    "Fujian": 1633.6,
    "Jiangxi": 2810.8,
    "Shandong": 9172.8,
    "Henan": 5257.6,
    "Hubei": 4362.5,
    "Hunan": 2617.1,
    "Guangdong": 5889.0,
    "Guangxi": 2958.3,
    "Hainan": 947.8,
    "Chongqing": 538.4,
    "Sichuan": 1638.7,
    "Guizhou": 2768.3,
    "Yunnan": 5200.6,
    "Tibet": 538.9,
    "Shaanxi": 4050.8,
    "Gansu": 3648.1,
    "Qinghai": 3868.7,
    "Ningxia": 3530.4,
    "Xinjiang": 7996.0,
}


def main() -> None:
    df = pd.read_csv(CSV)
    hist = ["2010", "2015", "2020"]
    df[hist] = df[hist].apply(pd.to_numeric, errors="coerce").fillna(0)
    baseline = df[hist].sum(axis=1)
    miss = sorted(set(df["Region"]) - set(NEA_万kW.keys()))
    if miss:
        raise SystemExit(f"CSV regions missing from NEA table: {miss}")
    for i, r in enumerate(df["Region"].values):
        cum_mw = float(NEA_万kW[r]) * 10.0
        d = cum_mw - float(baseline.iloc[i])
        df.at[i, "2025"] = round(max(d, 0.0), 3)
    df.to_csv(CSV, index=False, lineterminator="\n")
    print("Updated", CSV)


if __name__ == "__main__":
    main()
