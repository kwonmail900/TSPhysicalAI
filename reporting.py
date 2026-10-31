"""Self-contained experiment provenance, including the forecast warm-up history."""
import hashlib
import json


def attach_provenance(result, records, source):
    inputs = [dict(row) for row in records]
    canonical = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {
        **result,
        "source": source,
        "input_records": inputs,
        "metadata": {
            "program": "TS Physical AI Energy Lab", "version": "1.0",
            "source": source, "data_rows": len(inputs),
            "data_start": inputs[0]["timestamp"], "data_end": inputs[-1]["timestamp"],
            "input_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "hash_encoding": "UTF-8 JSON: sort_keys=true, separators=(',',':'), ensure_ascii=false",
            "scope": "가상 에너지 시스템 실험. 실제 캠퍼스 제어·절감 실증이 아닙니다.",
            "models": "계절 반복 / 과거 동일 시간대 평균. TSFM·LLM 미사용.",
        },
    }
