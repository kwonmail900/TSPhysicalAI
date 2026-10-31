import copy
import csv
import io
import math
import unittest

from energy_sim import (battery_step, forecast_at, generate_demo, parse_csv,
                        run_experiment, validate_config)


class PhysicsTests(unittest.TestCase):
    def test_capacity_power_efficiency_and_energy_conservation(self):
        cfg = validate_config({"capacity_kwh": 10, "max_power_kw": 2, "efficiency": .8})
        charged = battery_step(5, 100, 3, 1, cfg, 1)
        self.assertAlmostEqual(charged["charge_kwh"], 2)
        self.assertAlmostEqual(charged["energy"], 6.6)
        self.assertAlmostEqual(charged["import_kwh"], 4)
        empty = battery_step(1.2, -100, 3, 1, cfg, 1)
        self.assertAlmostEqual(empty["discharge_kwh"], .16)
        self.assertAlmostEqual(empty["energy"], 1)
        self.assertAlmostEqual(empty["import_kwh"], 1.84)
        full = battery_step(8.8, 100, 0, 2, cfg, 1)
        self.assertAlmostEqual(full["charge_kwh"], .25)
        self.assertAlmostEqual(full["energy"], 9)
        self.assertAlmostEqual(full["export_kwh"], 1.75)
        for step in (charged, empty, full):
            self.assertAlmostEqual(step["energy_balance_error_kwh"], 0)
            self.assertFalse(step["charge_kwh"] and step["discharge_kwh"])

    def test_all_policy_physics_and_whole_run_accounting(self):
        for resolution in ("hourly", "daily"):
            for scenario in ("normal", "load_surge", "pv_drop", "sensor_freeze"):
                result = run_experiment(generate_demo(resolution, 16), {"resolution": resolution, "scenario": scenario})
                cfg = result["config"]
                dt = result["evaluation"]["dt_hours"]
                for run, metrics in zip(result["runs"], result["comparison"]):
                    start = cfg["capacity_kwh"] * cfg["initial_soc"]
                    previous = start
                    for row in run["rows"]:
                        if run["policy"] == "no_ess":
                            self.assertIsNone(row["soc"])
                        else:
                            self.assertGreaterEqual(row["soc"], cfg["min_soc"] - 1e-10)
                            self.assertLessEqual(row["soc"], cfg["max_soc"] + 1e-10)
                        self.assertLessEqual(abs(row["applied_power_kw"]), cfg["max_power_kw"] + 1e-10)
                        self.assertGreaterEqual(row["import_kwh"], 0)
                        self.assertGreaterEqual(row["export_kwh"], 0)
                        self.assertAlmostEqual(row["import_kwh"] + row["pv_kwh"] + row["discharge_kwh"],
                                               row["load_kwh"] + row["charge_kwh"] + row["export_kwh"], places=7)
                        expected = previous + cfg["efficiency"] * row["charge_kwh"] - row["discharge_kwh"] / cfg["efficiency"]
                        if run["policy"] != "no_ess":
                            self.assertAlmostEqual(row["soc"] * cfg["capacity_kwh"], expected, places=7)
                        previous = expected
                    self.assertAlmostEqual(metrics["import_kwh"] + metrics["pv_kwh"] - metrics["export_kwh"],
                                           metrics["load_kwh"] + metrics["stored_energy_change_kwh"] + metrics["battery_loss_kwh"], places=6)

    def test_forecast_and_planning_do_not_use_future_truth(self):
        records = generate_demo(days=16)
        original = copy.deepcopy(records)
        target_index = 9 * 24 + 12
        mutated = copy.deepcopy(records)
        for row in mutated[target_index:]:
            row["load_kwh"] *= 5
            row["pv_kwh"] *= .1
        before = run_experiment(records)
        after = run_experiment(mutated)
        offset = target_index - 7 * 24
        for policy in ("day_ahead", "feedback"):
            old = next(r for r in before["runs"] if r["policy"] == policy)["rows"]
            new = next(r for r in after["runs"] if r["policy"] == policy)["rows"]
            self.assertEqual(old[:offset], new[:offset])
            for key in ("proposed_power_kw", "forecast_load_kwh", "forecast_pv_kwh", "decision_soc"):
                self.assertEqual(old[offset][key], new[offset][key], key)
        self.assertEqual(records, original)

    def test_weekday_mean_uses_available_history_only(self):
        records = generate_demo(days=35)
        f = forecast_at(records, 28 * 24, 28 * 24, 24, "weekday_mean")
        expected = sum(records[d * 24]["load_kwh"] for d in (0, 7, 14, 21)) / 4
        self.assertAlmostEqual(f["load_kwh"], expected)
        one = forecast_at(records, 7 * 24, 7 * 24, 24, "weekday_mean")
        self.assertEqual(one["load_kwh"], records[0]["load_kwh"])

    def test_scenario_is_common_and_sensor_freeze_does_not_stop_physics(self):
        result = run_experiment(generate_demo(days=16), {"scenario": "sensor_freeze"})
        rows = result["runs"][2]["rows"]
        frozen = [r for r in rows if "sensor_freeze" in r["event"]]
        self.assertEqual(len(frozen), 48)
        self.assertEqual(len({r["observed_soc"] for r in frozen}), 1)
        self.assertGreater(len({r["soc"] for r in frozen}), 1)
        for r in frozen:
            self.assertAlmostEqual(r["energy_balance_error_kwh"], 0)
            self.assertAlmostEqual(r["observed_net_kwh"], r["load_kwh"] - r["pv_kwh"])
        for run in result["runs"][1:]:
            self.assertEqual([(r["load_kwh"], r["pv_kwh"]) for r in run["rows"]],
                             [(r["load_kwh"], r["pv_kwh"]) for r in result["runs"][0]["rows"]])

    def test_soc_freeze_keeps_independent_meter_feedback_available(self):
        result = run_experiment(generate_demo(days=16), {"scenario": "sensor_freeze"})
        rows = next(run["rows"] for run in result["runs"] if run["policy"] == "feedback")
        start = next(i for i, row in enumerate(rows) if "sensor_freeze" in row["event"])
        # Resolve several intervals inside the freeze. Only previous meter readings may
        # contribute to this decision's forecast correction; target truth is excluded.
        index = start + 5
        previous = rows[max(0, index - 24):index]
        row = rows[index]
        for quantity in ("load", "pv"):
            bias = sum(r[f"{quantity}_kwh"] - r[f"base_forecast_{quantity}_kwh"]
                       for r in previous) / len(previous)
            expected = max(0.0, row[f"base_forecast_{quantity}_kwh"] + bias)
            self.assertAlmostEqual(row[f"forecast_{quantity}_kwh"], expected)
        self.assertEqual(row["decision_soc"], rows[start]["decision_soc"])
        self.assertGreater(len({r["observed_net_kwh"] for r in rows[start:index]}), 1)

    def test_daily_mode_is_daily_and_repeatable(self):
        records = generate_demo("daily", 15, 73)
        result = run_experiment(records, {"resolution": "daily"})
        self.assertEqual(result, run_experiment(records, {"resolution": "daily"}))
        self.assertEqual(result["evaluation"]["dt_hours"], 24)
        self.assertEqual(result["evaluation"]["steps"], 8)
        self.assertIn("피크 아님", result["evaluation"]["peak_note"])
        self.assertTrue(all(x["pv_self_consumption_rate"] is None for x in result["comparison"]))

    def test_csv_round_trip_and_reject_bad_data(self):
        records = generate_demo("daily", 15)
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=["timestamp", "load_kwh", "pv_kwh"])
        writer.writeheader()
        writer.writerows(records)
        self.assertEqual(parse_csv("\ufeff" + stream.getvalue(), "daily"), records)
        for bad in ("timestamp,load_kwh,pv_kwh\n2025-01-01,-1,2\n",
                    stream.getvalue().replace(str(records[0]["load_kwh"]), "nan", 1),
                    stream.getvalue().replace(records[1]["timestamp"], records[0]["timestamp"], 1),
                    stream.getvalue().replace("timestamp,load_kwh,pv_kwh", "timestamp,load_kw,pv_kw", 1),
                    stream.getvalue().replace(records[0]["timestamp"], records[0]["timestamp"] + "+09:00", 1)):
            with self.subTest(bad=bad[:80]), self.assertRaises(ValueError):
                parse_csv(bad, "daily")

    def test_invalid_config_is_rejected(self):
        for cfg in ({"capacity_kwh": 0}, {"efficiency": float("nan")}, {"max_power_kw": True},
                    {"initial_soc": 2}, {"min_soc": .7}, {"resolution": "quarter_hour"},
                    {"forecast_model": "tsfm"}, {"unknown": 2}, {"seed": 1.5}, {"scenario": []},
                    {"reserve_soc": None}, {"resolution": {}}, []):
            with self.subTest(cfg=cfg), self.assertRaises(ValueError):
                validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
