"""FM1.5 job APIs: no privilege escalation, UART use or firmware mutation."""
from __future__ import annotations

import http.client
import json
import ssl
import shutil
import subprocess
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from ywdfws.app import create_server
from ywdfws.config import StudioConfig, StudioConfigError, load, validate
from ywdfws.maintenance_jobs import JobManager, JobConflict
from ywdfws.security import PasswordVerifier, create_password_file

PASSWORD = "FM1-5-integration-tests-strong"


class ReadonlyPreflightTests(unittest.TestCase):
    def test_preflight_data_are_real_and_hardware_inert(self):
        manager = JobManager()
        reference = {
            "backup_access": "available", "backup_count": 1,
            "catalog_access": "privileged-readonly-broker",
            "backups": [{"kind": "golden_stock", "integrity": "verified", "restore_qualified": True}],
        }
        with patch("ywdfws.maintenance_jobs.broker_inventory", return_value=reference):
            started = manager.start("token-a")
            for _ in range(100):
                job = manager.get("token-a", started["id"])
                if job["status"] != "running": break
                time.sleep(.005)
        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["steps_completed"], 3)
        self.assertEqual(job["result"]["verified_golden_stock"], 1)
        self.assertEqual(job["result"]["physical_backup_performed"], False)
        self.assertEqual(job["result"]["hardware_backup_authorized"], False)
        self.assertFalse(job["hardware_access"])
        self.assertFalse(job["firmware_written"])
        self.assertEqual([event["seq"] for event in job["events"]], list(range(1, len(job["events"])+1)))
        self.assertIsNone(manager.get("another-session", started["id"]))

    def test_unprivileged_catalog_with_restricted_access_stays_limited(self):
        manager = JobManager()
        fallback = {
            "backup_access": "restricted", "backup_count": 0,
            "backups": [], "catalog_access": "unprivileged-local",
        }
        with patch("ywdfws.maintenance_jobs.broker_inventory", side_effect=PermissionError),              patch("ywdfws.maintenance_jobs.inventory", return_value=fallback):
            started = manager.start("token")
            for _ in range(100):
                job = manager.get("token", started["id"])
                if job["status"] != "running": break
                time.sleep(.005)
        self.assertEqual(job["status"], "limited")
        self.assertIsNone(job["result"]["verified_golden_stock"])
        self.assertIsNone(job["result"]["backup_count"])
        self.assertIn("counts are unknown", job["events"][-2]["message"])
        self.assertFalse(job["result"]["live_target_verified"])

    def test_only_one_concurrent_job(self):
        manager = JobManager()
        started_evt = threading.Event()
        release = threading.Event()

        def slow():
            started_evt.set()
            release.wait(1)
            return {"backup_access": "available", "backup_count": 0, "backups": []}

        try:
            with patch("ywdfws.maintenance_jobs.broker_inventory", side_effect=slow):
                first = manager.start("one")
                self.assertTrue(started_evt.wait(1))
                with self.assertRaises(JobConflict):
                    manager.start("two")
        finally:
            release.set()
        self.assertIsNotNone(manager.get("one", first["id"]))


class HTTPJobTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "pw"
        create_password_file(path, PASSWORD)
        cls.server = create_server(StudioConfig(port=0), PasswordVerifier(path))
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        conn.request(method, path, body=payload, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        code = resp.status
        hs = dict(resp.getheaders())
        conn.close()
        return code, json.loads(body), hs

    def login(self):
        code, body, h = self.request(
            "POST", "/api/login", json.dumps({"password": PASSWORD}),
            {"Content-Type": "application/json",
             "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(code, 200)
        return h["Set-Cookie"].split(";")[0], body["csrf"]

    def test_preflight_requires_session_csrf_same_origin_and_no_arguments(self):
        host = f"http://127.0.0.1:{self.port}"
        cookie, csrf = self.login()
        self.assertEqual(self.request("POST", "/api/jobs/preflight",
                        headers={"Origin": host})[0], 401)
        self.assertEqual(self.request("POST", "/api/jobs/preflight",
                        headers={"Origin": host, "Cookie": cookie})[0], 403)
        self.assertEqual(self.request("POST", "/api/jobs/preflight",
                        headers={"Origin": "http://evil.test", "Cookie": cookie,
                                 "X-CSRF-Token": csrf})[0], 403)
        self.assertEqual(self.request("POST", "/api/jobs/preflight", "{}",
                        {"Origin": host, "Cookie": cookie,
                         "X-CSRF-Token": csrf, "Content-Type": "application/json"})[0], 400)
        code, started, _ = self.request("POST", "/api/jobs/preflight",
                        headers={"Origin": host, "Cookie": cookie,
                                 "X-CSRF-Token": csrf})
        self.assertEqual(code, 202)
        self.assertEqual(started["mode"], "READ_ONLY_PREFLIGHT")
        self.assertFalse(started["hardware_access"])
        cookie2, _ = self.login()
        self.assertEqual(self.request("GET", "/api/jobs/" + started["id"],
                        headers={"Cookie": cookie2})[0], 404)
        for _ in range(100):
            code, record, _ = self.request("GET", "/api/jobs/" + started["id"],
                                            headers={"Cookie": cookie})
            if record["status"] != "running": break
            time.sleep(.01)
        self.assertEqual(code, 200)
        self.assertIn(record["status"], {"complete", "limited"})
        self.assertFalse(record["firmware_written"])
        self.assertFalse(record["hardware_access"])
        for endpoint in ("/api/jobs/backup", "/api/jobs/flash", "/api/jobs/restore"):
            self.assertEqual(self.request("POST", endpoint,
                            headers={"Origin": host, "Cookie": cookie,
                                     "X-CSRF-Token": csrf})[0], 404)


class HTTPSIntegrationTests(unittest.TestCase):
    def test_https_login_secure_cookie_and_strict_origin(self):
        if not shutil.which("openssl"):
            self.skipTest("openssl executable unavailable for temporary test certificate")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            key, cert, password = d / "key.pem", d / "cert.pem", d / "password.json"
            cmd = ["openssl", "req", "-x509", "-newkey", "rsa:2048",
                   "-nodes", "-sha256", "-days", "1",
                   "-subj", "/CN=127.0.0.1",
                   "-addext", "subjectAltName=IP:127.0.0.1",
                   "-keyout", str(key), "-out", str(cert)]
            subprocess.run(cmd, check=True, capture_output=True, timeout=20)
            key.chmod(0o600)
            create_password_file(password, PASSWORD)
            config = StudioConfig(port=0, tls_cert_file=cert, tls_key_file=key)
            server = create_server(config, PasswordVerifier(password))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                conn = http.client.HTTPSConnection("127.0.0.1", server.server_port,
                          timeout=3, context=ssl._create_unverified_context())
                origin = f"https://127.0.0.1:{server.server_port}"
                conn.request("POST", "/api/login", json.dumps({"password": PASSWORD}),
                             {"Origin": origin, "Content-Type": "application/json"})
                response = conn.getresponse()
                data = json.loads(response.read())
                self.assertEqual(response.status, 200)
                self.assertTrue(data["authenticated"])
                self.assertIn("Secure", response.getheader("Set-Cookie"))
                conn.close()
                conn = http.client.HTTPSConnection("127.0.0.1", server.server_port,
                          timeout=3, context=ssl._create_unverified_context())
                conn.request("POST", "/api/login", json.dumps({"password": PASSWORD}),
                             {"Origin": f"http://127.0.0.1:{server.server_port}",
                              "Content-Type": "application/json"})
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 403)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)

    def test_world_readable_tls_key_rejected(self):
        if not shutil.which("openssl"):
            self.skipTest("openssl unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            key, cert, password = d / "key.pem", d / "cert.pem", d / "password.json"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                            "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
                            "-keyout", str(key), "-out", str(cert)],
                           check=True, capture_output=True, timeout=20)
            key.chmod(0o644)
            create_password_file(password, PASSWORD)
            from ywdfws.security import SecurityError
            with self.assertRaises(SecurityError):
                create_server(StudioConfig(port=0, tls_cert_file=cert, tls_key_file=key),
                              PasswordVerifier(password))


class TLSConfigTests(unittest.TestCase):
    def test_tls_paths_must_be_paired(self):
        with self.assertRaises(StudioConfigError):
            validate(StudioConfig(tls_cert_file=Path("/tmp/cert.pem")))
        with self.assertRaises(StudioConfigError):
            validate(StudioConfig(tls_key_file=Path("/tmp/key.pem")))
        c = validate(StudioConfig(tls_cert_file=Path("/tmp/cert.pem"),
                                  tls_key_file=Path("/tmp/key.pem")))
        self.assertEqual(c.port, 8090)

    def test_tls_unknown_setting_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "fws.toml"
            p.write_text('[tls]\ncert_file="a"\nkey_file="b"\nextra=true\n')
            with self.assertRaises(StudioConfigError): load(p)


if __name__ == "__main__":
    unittest.main()
