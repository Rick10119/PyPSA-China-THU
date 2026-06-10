# SPDX-FileCopyrightText: 2026 Ruike Lyu
#
# SPDX-License-Identifier: MIT
"""Build a full Shandong negative-price config without overwriting config.yaml."""

from __future__ import annotations

import argparse
import copy
from pathlib import Path

import yaml


DEFAULT_BASE = Path("config.yaml")
DEFAULT_OVERLAY = Path("configs/shandong_negative_price_0609.1H.1.yaml")
DEFAULT_OUT = Path("configs/generated_shandong_negative_price_0609.1H.1.yaml")


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, default=DEFAULT_BASE)
    p.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    with args.base.open("r", encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}
    with args.overlay.open("r", encoding="utf-8") as f:
        overlay = yaml.safe_load(f) or {}
    merged = _deep_merge(base, overlay)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False, allow_unicode=True)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
