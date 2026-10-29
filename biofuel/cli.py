"""Run the prototype dynamic total dry-fuel-load model."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .fuel_model import run_fuel_model


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--landcover", type=Path, required=True)
    parser.add_argument("--dmp", type=Path, nargs="+", required=True, help="Ordered DMP GeoTIFFs; filenames contain YYYYMMDD")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--initial-fuel", type=Path)
    parser.add_argument("--delta-days", type=float, default=10.0)
    parser.add_argument("--dmp-units", help="Override DMP metadata, e.g. kg/ha/day or g/m2/day")
    parser.add_argument("--write-kgm2", action="store_true")
    parser.add_argument("--spinup", action="store_true")
    parser.add_argument("--spinup-cycles", type=int, default=3)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    outputs = run_fuel_model(
        args.dmp, args.landcover, args.output_dir,
        initial_fuel_path=args.initial_fuel,
        delta_days=args.delta_days,
        dmp_units=args.dmp_units,
        write_kgm2=args.write_kgm2,
        spinup=args.spinup,
        spinup_cycles=args.spinup_cycles,
    )
    for output in outputs:
        print(output)


if __name__ == "__main__":
    main()
