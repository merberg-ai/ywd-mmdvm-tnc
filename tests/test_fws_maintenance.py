"""FM1.5c host-only tests: privileged broker mocked, ZERO physical hardware access."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import ywdfws.maintenance_broker as mb
from ywdfws.app import create_server
from ywdfws.config import StudioConfig
from ywdfws.security import PasswordVerifier, create_password_file


PASSWORD = "fm15c-test-password-valid"
CONFIRM = "READ-HAT-MAIN-FLASH"


def wait(controller, job_id):
    for _ in range(100):
        snap = controller.snapshot(job_id)
        if snap["status"] != "running":
            return snap
        time.sleep(.01)
    raise AssertionError("mock maintenance job never reached a terminal state")


class RootAuthorizationTests(unittest.TestCase):
    def test_not_armed_by_default_and_never_issues_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = mb.BackupController(audit_path=Path(tmp) / "audit", enabled=False)
            self.assertFalse(ctrl.ready()["armed"])
            with self.assertRaises(mb.MaintenanceError):
                ctrl.begin("anything", CONFIRM)
            self.assertIsNone(ctrl.job)

    def test_bad_code_or_phrase_does_not_execute_and_attempts_burn_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            invoked = []
            ctrl = mb.BackupController(audit_path=Path(tmp)/"audit", enabled=True,
                                       executor=lambda progress: invoked.append(1))
            code = ctrl.code
            with self.assertRaisesRegex(mb.MaintenanceError, "denied"):
                ctrl.begin(code, "WRONG PHRASE")
            for _ in range(4):
                with self.assertRaisesRegex(mb.MaintenanceError, "denied"):
                    ctrl.begin("WRONG CODE", CONFIRM)
            self.assertFalse(ctrl.ready()["armed"])
            with self.assertRaises(mb.MaintenanceError):
                ctrl.begin(code, CONFIRM)
            self.assertEqual(invoked, [])

    def test_code_expiry_prevents_hardware(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = mb.BackupController(audit_path=Path(tmp)/"audit", enabled=True, lifetime=-1)
            with self.assertRaisesRegex(mb.MaintenanceError, "not armed"):
                ctrl.begin(ctrl.code, CONFIRM)
            self.assertIsNone(ctrl.job)

    def test_one_use_verified_capture_and_durable_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []

            def fake(progress):
                for stage in ("preflight","service_stop","probe","read_a","read_b","verify","recovery"):
                    progress("mock " + stage, stage)
                    events.append(stage)
                return {"verified": True, "kind": "captured_firmware", "sha256": "ab"*32}

            audit = Path(tmp)/"audit"
            ctrl = mb.BackupController(audit_path=audit, enabled=True, executor=fake)
            token = ctrl.code
            started = ctrl.begin(token, CONFIRM)
            snap = wait(ctrl, started["id"])
            self.assertEqual(snap["status"], "completed")
            self.assertEqual(snap["steps_completed"], 8)
            self.assertEqual(snap["backup_kind"], "captured_firmware")
            self.assertFalse(snap["flash_written"])
            self.assertFalse(snap["option_bytes_written"])
            self.assertEqual(len(events), 7)
            self.assertNotIn(token, audit.read_text())
            self.assertEqual(audit.stat().st_mode & 0o777, 0o600)
            self.assertEqual([x["event"] for x in map(json.loads, audit.read_text().splitlines())],
                             ["BACKUP_AUTHORIZED", "BACKUP_COMPLETED"])
            with self.assertRaises(mb.MaintenanceError):
                ctrl.begin(token, CONFIRM)
            self.assertIsNone(ctrl.snapshot("other"))

    def test_failure_records_and_never_claims_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            def failed(progress):
                progress("mock reboot", "recovery")
                raise RuntimeError("simulated UART read failure")
            ctrl = mb.BackupController(audit_path=Path(tmp)/"audit", enabled=True, executor=failed)
            started = ctrl.begin(ctrl.code, CONFIRM)
            snap = wait(ctrl, started["id"])
            self.assertEqual(snap["status"], "failed")
            self.assertFalse(snap["flash_written"])
            self.assertNotIn("sha256", {k for k,v in snap.items() if v})
            self.assertIn("BACKUP_FAILED", (Path(tmp)/"audit").read_text())

    def test_refuse_audit_symlink_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp)/"audit"
            audit.symlink_to(Path(tmp)/"elsewhere")
            ctrl = mb.BackupController(audit_path=audit, enabled=True,
                                       executor=lambda cb: self.fail("hardware called"))
            with self.assertRaises(mb.MaintenanceError):
                ctrl.begin(ctrl.code, CONFIRM)
            self.assertIsNone(ctrl.job)

    def test_broker_restricts_to_exact_local_opcode_and_peer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "maintenance.sock"
            ctrl = mb.BackupController(audit_path=Path(tmp)/"audit", enabled=True,
                           executor=lambda cb: {"verified": True, "kind": "captured_firmware", "sha256": "a"*64})
            uid = os.getuid()
            self.assertNotEqual(uid, 0, "Host-only test must run as nonroot")
            with patch.object(mb, "SOCKET_PATH", path), \
                 patch("ywdfws.maintenance_broker.os.geteuid", return_value=0), \
                 patch("ywdfws.maintenance_broker.os.chown"):
                server = mb.MaintenanceServer(path, uid, os.getgid(), ctrl)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                self.assertTrue(mb.request("READY", path=path)["armed"])
                with self.assertRaises(mb.MaintenanceError):
                    mb.request("WRITE_FLASH", path=path)
                started = mb.request("START_BACKUP", path=path,
                      authorization=ctrl.code, confirmation=CONFIRM)
                self.assertEqual(wait(ctrl, started["id"])["status"], "completed")
                self.assertEqual(mb.request("STATUS", path=path, job_id=started["id"])["status"], "completed")
                self.assertEqual(mb.request("STATUS", path=path, job_id="made-up")["error"], "unknown job")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                path.unlink(missing_ok=True)


class BrowserSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        p = self.directory / "pw"
        create_password_file(p, PASSWORD)
        self.verifier = PasswordVerifier(p)
        self.server = create_server(StudioConfig(port=0), self.verifier)
        self.port = self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, *, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        conn.request(method, path, body=payload, headers=headers or {})
        response = conn.getresponse()
        code = response.status
        data = json.loads(response.read())
        conn.close()
        return code, data

    def login(self):
        host = f"http://127.0.0.1:{self.port}"
        code, body = self.request("POST", "/api/login",
                payload=json.dumps({"password": PASSWORD}),
                headers={"Origin": host, "Content-Type": "application/json"})
        self.assertEqual(code, 200)
        # HTTP-only tests do not need to inspect cookies for rejected operations.
        return body["csrf"]

    def test_unencrypted_http_cannot_trigger_hardware_regardless_of_code(self):
        with patch("ywdfws.app.maintenance_request") as root:
            self.assertEqual(self.request("POST", "/api/maintenance/backup",
                headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 401)
            self.login()
            code, status = self.request("GET", "/api/maintenance/ready")
            self.assertEqual(code, 401)  # no session
            # Log in again to obtain a real session cookie and prove the
            # transport gate rejects even a valid authenticated request.
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
            origin = f"http://127.0.0.1:{self.port}"
            conn.request("POST", "/api/login",
                         body=json.dumps({"password": PASSWORD}),
                         headers={"Origin": origin, "Content-Type": "application/json"})
            response = conn.getresponse()
            csrf = json.loads(response.read())["csrf"]
            cookie = response.getheader("Set-Cookie").split(";")[0]
            conn.close()
            code, reply = self.request("POST", "/api/maintenance/backup",
                payload=json.dumps({"authorization": "faked-code",
                                    "confirmation": CONFIRM}),
                headers={"Origin": origin, "Cookie": cookie,
                         "X-CSRF-Token": csrf, "Content-Type": "application/json"})
            self.assertEqual(code, 403)
            self.assertIn("HTTPS required", reply["error"])
            self.assertFalse(root.called)


class HTTPSBackupTests(unittest.TestCase):
    def test_secure_route_requires_csrf_and_owner_session(self):
        if not shutil.which("openssl"):
            self.skipTest("openssl unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key, cert, pw = root/"key", root/"cert", root/"pw"
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                           "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
                           "-addext", "subjectAltName=IP:127.0.0.1",
                           "-keyout", str(key), "-out", str(cert)],
                           capture_output=True, check=True, timeout=30)
            key.chmod(0o600)
            create_password_file(pw, PASSWORD)
            server = create_server(StudioConfig(port=0, tls_cert_file=cert, tls_key_file=key),
                                   PasswordVerifier(pw))
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            host = f"https://127.0.0.1:{server.server_port}"

            def call(method, path, *, cookie=None, csrf=None, obj=None, origin=None):
                connection = http.client.HTTPSConnection("127.0.0.1", server.server_port,
                               context=ssl._create_unverified_context(), timeout=4)
                headers = {"Origin": origin if origin is not None else host}
                if cookie: headers["Cookie"] = cookie
                if csrf: headers["X-CSRF-Token"] = csrf
                if obj is not None: headers["Content-Type"] = "application/json"
                connection.request(method, path, body=json.dumps(obj) if obj is not None else None,
                                   headers=headers)
                response = connection.getresponse()
                code = response.status
                data = json.loads(response.read())
                cookie_out = response.getheader("Set-Cookie")
                connection.close()
                return code, data, cookie_out

            try:
                _, login, raw_cookie = call("POST", "/api/login", obj={"password": PASSWORD})
                cookie = raw_cookie.split(";")[0]
                self.assertIn("Secure", raw_cookie)
                self.assertEqual(call("POST", "/api/maintenance/backup",
                        cookie=cookie, obj={"authorization":"fake",
                        "confirmation":CONFIRM})[0], 403)
                self.assertEqual(call("POST", "/api/maintenance/backup",
                        cookie=cookie, csrf=login["csrf"], obj={"authorization":"fake",
                        "confirmation":CONFIRM}, origin="https://evil.test")[0], 403)
                with patch("ywdfws.app.maintenance_request") as worker:
                    worker.return_value = {"id":"testjob123", "status":"running","events":[]}
                    self.assertEqual(call("POST", "/api/maintenance/backup",
                        cookie=cookie, csrf=login["csrf"], obj={"authorization":"fake",
                        "confirmation":CONFIRM})[0], 202)
                    self.assertEqual(worker.call_args.args, ("START_BACKUP",))
                    self.assertEqual(call("GET", "/api/maintenance/backup/testjob123", cookie=cookie)[0], 200)
                    _, login2, cookie2 = call("POST", "/api/login", obj={"password": PASSWORD})
                    self.assertEqual(call("GET", "/api/maintenance/backup/testjob123",
                             cookie=cookie2.split(";")[0])[0], 404)
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
