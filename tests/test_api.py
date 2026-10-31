"""HTTP contract tests exercise actual application requests on a local server."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import Handler
from energy_sim import run_experiment


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def get(self, path):
        with urlopen(self.base + path, timeout=10) as response:
            return response.read(), response.headers

    def post(self, path, payload, **headers):
        request = Request(self.base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers})
        with urlopen(request, timeout=20) as response:
            return json.load(response)

    def test_home_and_assets(self):
        body, headers = self.get("/")
        self.assertIn(b"<!", body)
        self.assertIn("text/html", headers["Content-Type"])
        for path in ("/app.js", "/style.css"):
            self.assertTrue(self.get(path)[0])

    def test_health(self):
        self.assertEqual(json.loads(self.get("/api/health")[0])["status"], "ok")

    def test_demo_seed_controls_data(self):
        first = json.loads(self.get("/api/demo?seed=3")[0])
        repeated = json.loads(self.get("/api/demo?seed=3")[0])
        other = json.loads(self.get("/api/demo?seed=4")[0])
        self.assertEqual(first["records"], repeated["records"])
        self.assertNotEqual(first["records"], other["records"])
        with self.assertRaises(HTTPError) as error:
            self.get("/api/demo?seed=NaN")
        self.assertEqual(error.exception.code, 400)

    def test_hourly_full_workflow(self):
        demo = json.loads(self.get("/api/demo")[0])
        result = self.post("/api/run", {**demo, "config": {"resolution": "hourly"}})
        self.assertEqual(result["source"], "synthetic")
        self.assertEqual(len(result["comparison"]), 4)
        self.assertGreater(result["evaluation"]["steps"], 0)
        self.assertGreater(result["forecast_metrics"]["n"], 0)
        self.assertEqual(len(result["metadata"]["input_sha256"]), 64)
        self.assertEqual(run_experiment(result["input_records"], result["config"])["comparison"], result["comparison"])
        for run in result["runs"]:
            self.assertEqual(len(run["rows"]), result["evaluation"]["steps"])

    def test_template_upload_daily_run(self):
        raw = self.get("/api/template.csv?resolution=daily")[0].decode("utf-8-sig")
        data = self.post("/api/upload", {"csv_text": raw, "resolution": "daily"})
        self.assertEqual(data["source"], "uploaded")
        result = self.post("/api/run", {**data, "config": {"resolution": "daily", "scenario": "pv_drop"}})
        self.assertEqual(result["evaluation"]["dt_hours"], 24)
        self.assertTrue(result["metadata"]["scope"])

    def test_bad_upload_is_reported(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/api/upload", {"csv_text": "timestamp,load_kwh,pv_kwh\nbad,NaN,1", "resolution": "hourly"})
        self.assertEqual(error.exception.code, 400)
        self.assertIn("error", json.load(error.exception))

    def test_bad_run_and_cross_site(self):
        with self.assertRaises(HTTPError) as error:
            self.post("/api/run", {"records": [], "config": {}})
        self.assertEqual(error.exception.code, 400)
        with self.assertRaises(HTTPError) as error:
            self.post("/api/run", {}, Origin="https://example.com")
        self.assertEqual(error.exception.code, 403)

    def test_paths_are_not_filesystem_paths(self):
        for path in ("/server.py", "/.git/config", "/../server.py"):
            with self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
