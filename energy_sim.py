"""Transparent, dependency-free teaching simulator (energy at the AC bus in kWh).

This is an educational battery model, not a calibrated campus digital twin.
Positive control power charges the battery; negative power discharges it.
"""
from __future__ import annotations

import csv
import io
import math
import random
import statistics
from datetime import datetime, timedelta


DEFAULTS = {
    "resolution": "hourly", "capacity_kwh": 100.0, "max_power_kw": 30.0,
    "initial_soc": 0.5, "min_soc": 0.1, "max_soc": 0.9,
    "efficiency": 0.95, "reserve_soc": 0.3,
    "forecast_model": "seasonal_naive", "scenario": "normal",
    "scenario_strength": 0.4, "seed": 42,
}
POLICIES = (
    ("no_ess", "ESS 없음"),
    ("reactive", "현재값 반응 제어"),
    ("day_ahead", "예측 계획 유지"),
    ("feedback", "예측 + 피드백 재계획"),
)


def _resolution(value):
    if value not in ("hourly", "daily"):
        raise ValueError("시간 해상도는 hourly 또는 daily여야 합니다.")
    return (24, 1.0) if value == "hourly" else (1, 24.0)


def _numeric(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name}: 유한한 숫자를 입력하세요.")
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"{name}: 유한한 숫자를 입력하세요.") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name}: 유한한 숫자를 입력하세요.")
    return value


def validate_config(config=None):
    result = dict(DEFAULTS)
    if config is not None:
        if not isinstance(config, dict):
            raise ValueError("설정은 JSON 객체여야 합니다.")
        unknown = set(config) - set(DEFAULTS)
        if unknown:
            raise ValueError("알 수 없는 설정: " + ", ".join(sorted(unknown)))
        result.update(config)
    _resolution(result["resolution"])
    for name in ("capacity_kwh", "max_power_kw", "initial_soc", "min_soc",
                 "max_soc", "efficiency", "reserve_soc", "scenario_strength"):
        result[name] = _numeric(result[name], name)
    if not 0 < result["capacity_kwh"] <= 1e9 or not 0 < result["max_power_kw"] <= 1e9:
        raise ValueError("ESS 용량과 출력은 0보다 크고 1e9 이하여야 합니다.")
    if not 0 <= result["min_soc"] < result["max_soc"] <= 1:
        raise ValueError("SOC 하한 < 상한이어야 하며 범위는 0~1입니다.")
    for name in ("initial_soc", "reserve_soc"):
        if not result["min_soc"] <= result[name] <= result["max_soc"]:
            raise ValueError(f"{name}은 SOC 하한과 상한 사이여야 합니다.")
    if not 0 < result["efficiency"] <= 1:
        raise ValueError("단방향 효율은 0보다 크고 1 이하여야 합니다.")
    if not 0 <= result["scenario_strength"] <= 1:
        raise ValueError("외란 강도는 0~1이어야 합니다.")
    if result["forecast_model"] not in ("seasonal_naive", "weekday_mean"):
        raise ValueError("지원하지 않는 예측 모델입니다.")
    if result["scenario"] not in ("normal", "load_surge", "pv_drop", "sensor_freeze"):
        raise ValueError("지원하지 않는 실험 시나리오입니다.")
    seed = _numeric(result["seed"], "seed")
    if not seed.is_integer() or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed는 0~4294967295의 정수여야 합니다.")
    result["seed"] = int(seed)
    return result


def _validate_records(records, resolution):
    slots, dt = _resolution(resolution)
    if not isinstance(records, list) or not 15 * slots <= len(records) <= 20000:
        raise ValueError(f"자료는 최소 15일({15 * slots}행), 최대 20,000행이어야 합니다.")
    parsed = []
    previous = None
    for index, item in enumerate(records, 2):
        if not isinstance(item, dict):
            raise ValueError(f"{index}행: 올바른 자료 객체가 아닙니다.")
        try:
            time = datetime.fromisoformat(str(item["timestamp"]).strip())
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"{index}행: timestamp는 ISO 날짜·시간 형식이어야 합니다.") from exc
        if time.tzinfo is not None:
            raise ValueError(f"{index}행: 시간대 없는 동일 현지 시각을 사용하세요.")
        if time.minute or time.second or time.microsecond or (resolution == "daily" and time.hour):
            raise ValueError(f"{index}행: 시간별 자료는 정시, 일별 자료는 날짜(00:00)여야 합니다.")
        if index == 2 and time.hour:
            raise ValueError("첫 자료는 00:00에서 시작해야 합니다.")
        if previous is not None and time - previous != timedelta(hours=dt):
            raise ValueError(f"{index}행: 중복·결측·역순 시각이 있습니다. 연속된 시간축이 필요합니다.")
        try:
            load = _numeric(item["load_kwh"], f"{index}행 load_kwh")
            pv = _numeric(item["pv_kwh"], f"{index}행 pv_kwh")
        except KeyError as exc:
            raise ValueError(f"{index}행: load_kwh와 pv_kwh 열이 필요합니다.") from exc
        if not 0 <= load <= 1e12 or not 0 <= pv <= 1e12:
            raise ValueError(f"{index}행: 에너지는 0~1e12 kWh여야 합니다.")
        parsed.append({"timestamp": time.isoformat(timespec="minutes"), "load_kwh": load, "pv_kwh": pv})
        previous = time
    if len(parsed) % slots:
        raise ValueError("자료는 온전한 일수로 구성해야 합니다(시간별 자료: 하루 24행).")
    return parsed


def parse_csv(text, resolution="hourly"):
    """Strict contiguous local-time CSV; no interpolation or silent row removal."""
    if not isinstance(text, str) or len(text.encode("utf-8")) > 5_000_000:
        raise ValueError("CSV는 UTF-8 텍스트이며 5 MB 이하여야 합니다.")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    headers = reader.fieldnames
    if not headers or len(set(headers)) != len(headers):
        raise ValueError("CSV 헤더가 없거나 중복되어 있습니다.")
    if not {"timestamp", "load_kwh", "pv_kwh"}.issubset(headers):
        raise ValueError("필수 CSV 열: timestamp,load_kwh,pv_kwh")
    records = []
    try:
        for row in reader:
            if None in row or any(row.get(name) is None for name in headers):
                raise ValueError("CSV 열 개수가 헤더와 일치하지 않습니다.")
            records.append(row)
            if len(records) > 20000:
                raise ValueError("최대 20,000행까지 지원합니다.")
    except csv.Error as exc:
        raise ValueError("CSV 형식을 확인하세요.") from exc
    return _validate_records(records, resolution)


def generate_demo(resolution="hourly", days=42, seed=42):
    """Deterministic synthetic campus-like data, unrelated to the uploaded paper."""
    slots, _ = _resolution(resolution)
    if isinstance(days, bool) or not isinstance(days, int) or not 15 <= days <= 365:
        raise ValueError("시연 자료는 15~365일 범위에서 생성합니다.")
    rng = random.Random(seed)
    origin = datetime(2025, 1, 6)
    records = []
    for day in range(days):
        date = origin + timedelta(days=day)
        cloud = rng.uniform(0.55, 1.0)
        holiday_factor = 0.65 if date.weekday() >= 5 else 1.0
        hourly = []
        for hour in range(24):
            active = 9 <= hour <= 18
            load = max(0, (10 + (17 if active else 2)) * holiday_factor + rng.gauss(0, 1.3))
            solar = max(0.0, math.sin(math.pi * (hour - 6) / 12)) if 6 < hour < 18 else 0.0
            pv = 45 * cloud * solar
            hourly.append((load, pv))
        if slots == 1:
            records.append({"timestamp": date.isoformat(timespec="minutes"),
                            "load_kwh": sum(x[0] for x in hourly),
                            "pv_kwh": sum(x[1] for x in hourly)})
        else:
            for hour, (load, pv) in enumerate(hourly):
                records.append({"timestamp": (date + timedelta(hours=hour)).isoformat(timespec="minutes"),
                                "load_kwh": load, "pv_kwh": pv})
    return records


def forecast_at(records, target, cutoff, slots, model):
    """A forecast for target, restricted strictly to indices less than cutoff."""
    lags = [7] if model == "seasonal_naive" else [7, 14, 21, 28]
    indices = [target - lag * slots for lag in lags if 0 <= target - lag * slots < cutoff]
    if not indices:
        raise ValueError("예측에 필요한 과거 같은 요일 자료가 없습니다.")
    return {key: statistics.fmean(records[index][key] for index in indices)
            for key in ("load_kwh", "pv_kwh")}


def battery_step(energy, proposed_power, load, pv, config, dt):
    """Apply physical limits independently of the policy and return bus energies."""
    cap = config["capacity_kwh"]
    eta = config["efficiency"]
    max_energy = config["max_power_kw"] * dt
    charge = discharge = 0.0
    if proposed_power >= 0:
        charge = max(0.0, min(proposed_power * dt, max_energy,
                              (cap * config["max_soc"] - energy) / eta))
    else:
        discharge = max(0.0, min(-proposed_power * dt, max_energy,
                                 (energy - cap * config["min_soc"]) * eta))
    new_energy = energy + eta * charge - discharge / eta
    net = load - pv + charge - discharge
    imported, exported = max(net, 0.0), max(-net, 0.0)
    balance = imported + pv + discharge - load - charge - exported
    applied = (charge - discharge) / dt
    return {"energy": new_energy, "charge_kwh": charge, "discharge_kwh": discharge,
            "import_kwh": imported, "export_kwh": exported,
            "applied_power_kw": applied, "energy_balance_error_kwh": balance,
            "clipped": abs(applied - proposed_power) > 1e-8}


def _plan(forecasts, sensed_energy, config, dt):
    """Heuristic PV-first peak shaving; deliberately not an optimizer or MPC."""
    positive = [max(0.0, f["load_kwh"] - f["pv_kwh"]) for f in forecasts]
    target = statistics.fmean(positive) if len(positive) > 1 else positive[0] * 0.55
    plans = []
    energy = sensed_energy
    for item in forecasts:
        net = item["load_kwh"] - item["pv_kwh"]
        if net < 0:
            requested = -net / dt
        else:
            available = max(0, energy - config["capacity_kwh"] * config["reserve_soc"])
            requested = -min(max(0, net - target), available * config["efficiency"]) / dt
        simulated = battery_step(energy, requested, item["load_kwh"], item["pv_kwh"], config, dt)
        energy = simulated["energy"]
        plans.append(simulated["applied_power_kw"])
    return plans


def run_experiment(records, config=None):
    config = validate_config(config)
    records = _validate_records(records, config["resolution"])
    slots, dt = _resolution(config["resolution"])
    warmup = 7 * slots
    count = len(records) - warmup
    disturbance_start = warmup + (count // (2 * slots)) * slots
    disturbance_end = min(len(records), disturbance_start + 2 * slots)
    scenario = config["scenario"]
    strength = config["scenario_strength"]
    # Copies keep callers' source records immutable. History is updated only after steps resolve.
    actual = [dict(row) for row in records]
    for index in range(disturbance_start, disturbance_end):
        if scenario == "load_surge":
            actual[index]["load_kwh"] *= 1 + strength
        elif scenario == "pv_drop":
            actual[index]["pv_kwh"] *= 1 - strength

    all_runs = []
    comparison = []
    errors = {"load_kwh": [], "pv_kwh": []}
    denominators = {"load_kwh": 0.0, "pv_kwh": 0.0}
    fixed_forecasts = {}
    for day in range(warmup, len(actual), slots):
        for target in range(day, day + slots):
            f = forecast_at(actual, target, day, slots, config["forecast_model"])
            fixed_forecasts[target] = f
            for key in errors:
                errors[key].append(abs(actual[target][key] - f[key]))
                denominators[key] += actual[target][key]

    for policy, label in POLICIES:
        energy = config["capacity_kwh"] * config["initial_soc"]
        observed_soc = config["initial_soc"]
        day_plan = []
        residual_history = []
        rows = []
        for index in range(warmup, len(actual)):
            row = actual[index]
            day_start = index - index % slots
            horizon_end = min(day_start + slots, len(actual))
            frozen = (scenario == "sensor_freeze" and disturbance_start <= index < disturbance_end)
            # Sensed state at decision time is the previous interval's end state.
            sensed_soc = observed_soc
            if index == day_start:
                f_day = [fixed_forecasts[t] for t in range(index, horizon_end)]
                day_plan = _plan(f_day, sensed_soc * config["capacity_kwh"], config, dt)
            base_forecast = fixed_forecasts[index]
            used_forecast = dict(base_forecast)
            if policy == "no_ess":
                proposed = 0.0
            elif policy == "reactive":
                # Independent load/PV meters remain valid even if the SOC sensor freezes.
                net = row["load_kwh"] - row["pv_kwh"]
                if net >= 0:
                    available = max(0.0, sensed_soc - config["min_soc"]) * config["capacity_kwh"]
                    proposed = -min(net, available * config["efficiency"]) / dt
                else:
                    room = max(0.0, config["max_soc"] - sensed_soc) * config["capacity_kwh"]
                    proposed = min(-net, room / config["efficiency"]) / dt
            elif policy == "day_ahead":
                proposed = day_plan[index - day_start]
            else:
                # Replan with recent *resolved* forecast residuals; no target measurements.
                recent = residual_history[-slots:]
                biases = {key: statistics.fmean(x[key] for x in recent) if recent else 0.0
                          for key in ("load_kwh", "pv_kwh")}
                future = []
                for target in range(index, horizon_end):
                    future.append({key: max(0.0, fixed_forecasts[target][key] + biases[key])
                                   for key in biases})
                used_forecast = future[0]
                proposed = _plan(future, sensed_soc * config["capacity_kwh"], config, dt)[0]
            state = battery_step(energy, proposed, row["load_kwh"], row["pv_kwh"], config, dt)
            old_energy = energy
            energy = state["energy"]
            net = row["load_kwh"] - row["pv_kwh"]
            observed_net = net
            if not frozen:
                observed_soc = energy / config["capacity_kwh"]
            # Load/PV meter readings resolve after the interval regardless of SOC sensor health.
            # They become available to the next decision, never to this interval's forecast plan.
            residual_history.append({key: row[key] - base_forecast[key]
                                     for key in ("load_kwh", "pv_kwh")})
            event = []
            if disturbance_start <= index < disturbance_end and scenario != "normal":
                event.append(scenario)
            if state["clipped"]:
                event.append("constraint_clip")
            rows.append({"timestamp": row["timestamp"], "load_kwh": row["load_kwh"],
                         "pv_kwh": row["pv_kwh"], "forecast_load_kwh": used_forecast["load_kwh"],
                         "forecast_pv_kwh": used_forecast["pv_kwh"],
                         "base_forecast_load_kwh": base_forecast["load_kwh"],
                         "base_forecast_pv_kwh": base_forecast["pv_kwh"],
                         "soc_before": old_energy / config["capacity_kwh"],
                         "soc": energy / config["capacity_kwh"], "observed_soc": observed_soc,
                         "decision_soc": sensed_soc, "observed_net_kwh": observed_net,
                         "proposed_power_kw": proposed, "event": ";".join(event),
                         **{key: value for key, value in state.items() if key != "energy"}})
            if policy == "no_ess":
                for key in ("soc_before", "soc", "observed_soc", "decision_soc"):
                    rows[-1][key] = None
        total_pv = sum(r["pv_kwh"] for r in rows)
        total_charge = sum(r["charge_kwh"] for r in rows)
        total_discharge = sum(r["discharge_kwh"] for r in rows)
        metrics = {"policy": policy, "label": label,
                   "import_kwh": sum(r["import_kwh"] for r in rows),
                   "export_kwh": sum(r["export_kwh"] for r in rows),
                   "pv_self_consumption_rate": sum(min(r["pv_kwh"], r["load_kwh"] + r["charge_kwh"])
                                                   for r in rows) / total_pv if total_pv and slots == 24 else None,
                   "peak_import_kw": max(r["import_kwh"] / dt for r in rows),
                   "final_soc": energy / config["capacity_kwh"] if policy != "no_ess" else None,
                   "constraint_clips": sum(r["clipped"] for r in rows),
                   "energy_balance_max_error_kwh": max(abs(r["energy_balance_error_kwh"]) for r in rows),
                   "cycles_equivalent": (total_charge * config["efficiency"] + total_discharge / config["efficiency"])
                                          / (2 * config["capacity_kwh"]),
                   "battery_loss_kwh": total_charge * (1 - config["efficiency"])
                                       + total_discharge * (1 / config["efficiency"] - 1),
                   "stored_energy_change_kwh": energy - config["capacity_kwh"] * config["initial_soc"],
                   "initial_energy_kwh": config["capacity_kwh"] * config["initial_soc"] if policy != "no_ess" else None,
                   "final_energy_kwh": energy if policy != "no_ess" else None,
                   "initial_soc": config["initial_soc"] if policy != "no_ess" else None,
                   "load_kwh": sum(r["load_kwh"] for r in rows), "pv_kwh": total_pv}
        comparison.append(metrics)
        all_runs.append({"policy": policy, "label": label, "rows": rows})
    return {
        "config": config, "resolution": config["resolution"], "forecast_model": config["forecast_model"],
        "data_note": "교육용 ESS 에너지 수지 모델. 효율·용량·출력 제한을 적용하며 실물 검증 또는 최적해를 주장하지 않습니다. sensor_freeze는 SOC 센서만 정체시키며 독립적인 부하·태양광 계량값은 정상 수신합니다.",
        "comparison": comparison, "runs": all_runs,
        "forecast_metrics": {
            "load_mae_kwh": statistics.fmean(errors["load_kwh"]),
            "pv_mae_kwh": statistics.fmean(errors["pv_kwh"]),
            "load_wape": sum(errors["load_kwh"]) / denominators["load_kwh"] if denominators["load_kwh"] else None,
            "pv_wape": sum(errors["pv_kwh"]) / denominators["pv_kwh"] if denominators["pv_kwh"] else None,
            "n": count, "note": "일 시작 시 과거 자료만 사용한 기본 예측; 피드백 보정 전 평가"},
        "evaluation": {"start": actual[warmup]["timestamp"], "end": actual[-1]["timestamp"],
                       "steps": count, "warmup_steps": warmup, "dt_hours": dt,
                       "disturbance_start": actual[disturbance_start]["timestamp"],
                       "disturbance_end_exclusive": (datetime.fromisoformat(actual[disturbance_end - 1]["timestamp"])
                                                     + timedelta(hours=dt)).isoformat(timespec="minutes"),
                       "peak_note": "1시간 평균 구매전력의 최대값" if slots == 24 else "일평균 구매전력의 최대값(시간대별 피크 아님)",
                       "limitations": "동일 초기 SOC이지만 종료 SOC는 다를 수 있습니다. 구매량만으로 정책 우열·절감률을 판정하지 말고 종료 저장량·손실을 함께 비교하세요. 일별 모드는 하루 안의 동시 수급을 가정한 집계 수지 실험이며 실제 자가소비율·피크를 추정하지 않습니다."},
    }
