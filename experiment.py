#!/usr/bin/env python3
"""Reproducible headless experiment export for teaching and research pilots."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from energy_sim import generate_demo, parse_csv, run_experiment
from reporting import attach_provenance


def main() -> None:
    parser = argparse.ArgumentParser(description="에너지 운영 비교실험 CSV·JSON 저장")
    parser.add_argument("--data", type=Path, help="timestamp,load_kwh,pv_kwh CSV; 생략하면 합성 자료")
    parser.add_argument("--resolution", choices=["hourly", "daily"], default="hourly")
    parser.add_argument("--model", choices=["seasonal_naive", "weekday_mean"], default="seasonal_naive")
    parser.add_argument("--scenario", choices=["normal", "load_surge", "pv_drop", "sensor_freeze"], default="normal")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    records = parse_csv(args.data.read_text(encoding="utf-8-sig"), args.resolution) if args.data else generate_demo(args.resolution, seed=args.seed)
    result = run_experiment(records, {"resolution": args.resolution, "forecast_model": args.model, "scenario": args.scenario, "seed": args.seed})
    result = attach_provenance(result, records, "uploaded" if args.data else "synthetic")
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "experiment.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for name, rows in [("comparison", result["comparison"])] + [(run["policy"], run["rows"]) for run in result["runs"]]:
        with (args.output / f"{name}.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"출처: {result['source']} / 평가 구간: {result['evaluation']['start']} ~ {result['evaluation']['end']}")
    print(f"저장 위치: {args.output.resolve()}")
    for row in result["comparison"]:
        final_soc = "해당 없음" if row["final_soc"] is None else f"{row['final_soc']:.1%}"
        print(f"{row['label']}: 구매 {row['import_kwh']:.2f} kWh, 잉여 {row['export_kwh']:.2f} kWh, 최종 SOC {final_soc}")


if __name__ == "__main__":
    main()
