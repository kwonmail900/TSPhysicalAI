#!/usr/bin/env python3
"""Local, dependency-free teaching application. No hardware or external API required."""
from __future__ import annotations

import argparse
import csv
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from energy_sim import generate_demo, parse_csv, run_experiment
from reporting import attach_provenance

ROOT = Path(__file__).resolve().parent
MAX_BODY_BYTES = 4 * 1024 * 1024
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "TSEnergyLab/1.0"

    def send_bytes(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def json_response(self, value: object, status: int = 200) -> None:
        self.send_bytes(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:
        url = urlparse(self.path)
        if url.path in STATIC:
            name, content_type = STATIC[url.path]
            self.send_bytes((ROOT / "static" / name).read_bytes(), content_type)
            return
        if url.path == "/api/health":
            self.json_response({"status": "ok", "mode": "simulation", "version": "1.0"})
            return
        if url.path in ("/api/demo", "/api/template.csv"):
            query = parse_qs(url.query)
            resolution = query.get("resolution", ["hourly"])[0]
            if resolution not in ("hourly", "daily"):
                self.json_response({"error": "시간 간격은 hourly 또는 daily여야 합니다."}, 400)
                return
            try:
                seed = int(query.get("seed", ["42"])[0])
                if not 0 <= seed <= 2**32 - 1:
                    raise ValueError()
            except ValueError:
                self.json_response({"error": "시드는 0~4294967295의 정수여야 합니다."}, 400)
                return
            records = generate_demo(resolution=resolution, seed=seed)
            if url.path == "/api/demo":
                self.json_response({"records": records, "source": "synthetic", "resolution": resolution, "seed": seed})
            else:
                buffer = io.StringIO(newline="")
                writer = csv.DictWriter(buffer, fieldnames=["timestamp", "load_kwh", "pv_kwh"])
                writer.writeheader()
                writer.writerows(records)
                self.send_bytes(buffer.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8")
            return
        self.json_response({"error": "요청한 경로가 없습니다."}, 404)

    def do_POST(self) -> None:
        if self.path not in ("/api/upload", "/api/run"):
            self.json_response({"error": "요청한 경로가 없습니다."}, 404)
            return
        if self.headers.get_content_type() != "application/json":
            self.json_response({"error": "JSON 형식으로 요청해 주세요."}, 415)
            return
        # Browser requests from another site cannot mutate or read this local lab.
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers.get("Host", ""):
            self.json_response({"error": "다른 사이트에서 보낸 요청은 허용하지 않습니다."}, 403)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("요청 크기는 1바이트 이상 4MB 이하여야 합니다.")
            payload = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("유한한 숫자를 입력해 주세요.")))
            if not isinstance(payload, dict):
                raise ValueError("요청은 JSON 객체여야 합니다.")
            if self.path == "/api/upload":
                csv_text = payload.get("csv_text")
                if not isinstance(csv_text, str):
                    raise ValueError("CSV 텍스트가 필요합니다.")
                resolution = payload.get("resolution", "hourly")
                records = parse_csv(csv_text, resolution=resolution)
                self.json_response({"records": records, "source": "uploaded", "resolution": resolution})
                return
            records = payload.get("records")
            config = payload.get("config", {})
            source = payload.get("source", "uploaded")
            if not isinstance(config, dict) or not isinstance(records, list):
                raise ValueError("config 객체와 records 배열이 필요합니다.")
            if source not in ("synthetic", "uploaded"):
                raise ValueError("자료 출처는 synthetic 또는 uploaded여야 합니다.")
            # Canonical CSV validation also protects direct API calls, not only uploads.
            if not 1 <= len(records) <= 20000:
                raise ValueError("자료는 최대 20,000행까지 지원합니다.")
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=["timestamp", "load_kwh", "pv_kwh"])
            writer.writeheader()
            for row in records:
                if not isinstance(row, dict) or not all(k in row for k in writer.fieldnames):
                    raise ValueError("각 행에 timestamp, load_kwh, pv_kwh가 필요합니다.")
                writer.writerow({key: row[key] for key in writer.fieldnames})
            canonical = parse_csv(buffer.getvalue(), resolution=config.get("resolution", "hourly"))
            result = attach_provenance(run_experiment(canonical, config), canonical, source)
            self.json_response(result)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            self.json_response({"error": str(exc) or "입력 형식을 확인해 주세요."}, 400)


def main() -> None:
    parser = argparse.ArgumentParser(description="시계열 기반 Physical AI 교육용 에너지 시뮬레이션")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer((args.host, args.port), Handler) as server:
        print(f"TS Energy Lab: http://{args.host}:{server.server_port}", flush=True)
        print("종료: Ctrl+C | Python 표준 라이브러리만 사용 | 외부 API 없음", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
