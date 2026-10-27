"""FM0 security and host-only HTTP contract. No hardware access required."""
from __future__ import annotations

import http.client
from pathlib import Path
import json
import tempfile
import threading
import unittest

from ywdfws.app import AuthState, create_server
from ywdfws.config import StudioConfig, StudioConfigError, load, validate
from ywdfws.security import PasswordVerifier, SecurityError, create_password_file


PASSWORD = "correct-test-secret-password-123"


class ConfigTests(unittest.TestCase):
    def test_safe_defaults(self):
        c = validate(StudioConfig())
        self.assertEqual((c.listen, c.port), ("127.0.0.1", 8090))

    def test_reject_wildcard_public_and_lan_listeners(self):
        for host in ("0.0.0.0", "192.168.1.11", "8.8.8.8", "::1", "example.org"):
            with self.subTest(host=host), self.assertRaises(StudioConfigError):
                validate(StudioConfig(listen=host))

    def test_explicit_rfc1918_lan_opt_in(self):
        for host in ("10.0.0.1", "10.255.255.254", "172.16.0.1",
                     "172.31.255.254", "192.168.1.11"):
            with self.subTest(host=host):
                cfg = validate(StudioConfig(listen=host, allow_private_bind=True))
                self.assertTrue(cfg.allow_private_bind)
        for host in ("0.0.0.0", "8.8.8.8", "1.1.1.1", "100.64.0.1",
                     "169.254.1.5", "172.32.0.1", "255.255.255.255", "::1"):
            with self.subTest(host=host), self.assertRaises(StudioConfigError):
                validate(StudioConfig(listen=host, allow_private_bind=True))
        with self.assertRaises(StudioConfigError):
            validate(StudioConfig(listen="192.168.1.11", allow_private_bind="true"))

    def test_toml_private_bind_requires_both_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "fws.toml"
            p.write_text('[web]\nlisten="192.168.1.11"\nport=8090\n')
            with self.assertRaises(StudioConfigError):
                load(p)
            p.write_text('[web]\nlisten="192.168.1.11"\nport=8090\nallow_private_bind=true\n')
            self.assertEqual(load(p).listen, "192.168.1.11")
            p.write_text('[web]\nlisten="0.0.0.0"\nallow_private_bind=true\n')
            with self.assertRaises(StudioConfigError):
                load(p)

    def test_reject_invalid_port_and_session_timeout(self):
        for port in (0, 65536, True, "8090"):
            with self.subTest(port=port), self.assertRaises(StudioConfigError):
                validate(StudioConfig(port=port))
        with self.assertRaises(StudioConfigError):
            validate(StudioConfig(session_minutes=0))

    def test_load_requires_explicit_known_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.toml"
            p.write_text('[web]\nlisten="127.0.0.1"\nport=8099\n[auth]\nsession_minutes=15\n')
            c = load(p)
            self.assertEqual(c.port, 8099)
            p.write_text('[web]\nlisten="0.0.0.0"\n')
            with self.assertRaises(StudioConfigError):
                load(p)
            p.write_text('[web]\nlisten="127.0.0.1"\nallow_wildcard_bind=true\n')
            with self.assertRaises(StudioConfigError):
                load(p)


class PasswordTests(unittest.TestCase):
    def test_scrypt_credentials_are_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "secret.json"
            create_password_file(p, PASSWORD)
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(PASSWORD, p.read_text())
            verifier = PasswordVerifier(p)
            self.assertTrue(verifier.verify(PASSWORD))
            self.assertFalse(verifier.verify("wrong"))
            with self.assertRaises(FileExistsError):
                create_password_file(p, PASSWORD)

    def test_password_minimum_and_secret_file_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "secret.json"
            with self.assertRaises(SecurityError):
                create_password_file(p, "short")
            create_password_file(p, PASSWORD)
            p.chmod(0o644)
            with self.assertRaises(SecurityError):
                PasswordVerifier(p)

    def test_expiring_sessions_and_rate_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "secret.json"
            create_password_file(p, PASSWORD)
            auth = AuthState(PasswordVerifier(p), session_minutes=5)
            token, csrf = auth.attempt("local", PASSWORD)
            self.assertIsNotNone(auth.get(token))
            self.assertNotEqual(token, csrf)
            auth.logout(token)
            self.assertIsNone(auth.get(token))
            for _ in range(5):
                self.assertIsNone(auth.attempt("local", "wrong"))
            self.assertTrue(auth.blocked("local"))
            self.assertIsNone(auth.attempt("local", PASSWORD))


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        secret = Path(cls.tmp.name) / "secret.json"
        create_password_file(secret, PASSWORD)
        # Port zero is only used by test setup; production config rejects it.
        cls.server = create_server(StudioConfig(port=0), PasswordVerifier(secret))
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.tmp.cleanup()

    def request(self, method, target, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=4)
        conn.request(method, target, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read()
        result = (response.status, json.loads(data) if response.getheader("Content-Type", "").startswith("application/json") else data,
                  dict(response.getheaders()))
        conn.close()
        return result

    def login(self, password=PASSWORD):
        host = f"http://127.0.0.1:{self.port}"
        return self.request("POST", "/api/login", json.dumps({"password": password}),
                            {"Content-Type": "application/json", "Origin": host})

    def test_login_requires_same_origin(self):
        code, _, _ = self.request("POST", "/api/login", json.dumps({"password": PASSWORD}), {"Content-Type": "application/json", "Origin": "http://evil.example"})
        self.assertEqual(code, 403)

    def test_invalid_login_and_auth_required(self):
        code, _, _ = self.login("incorrect-password")
        self.assertEqual(code, 401)
        code, body, _ = self.request("GET", "/api/status")
        self.assertEqual(code, 401)
        self.assertNotIn("flash_enabled", body)

    def test_session_cookie_and_csrf_logout(self):
        code, body, headers = self.login()
        self.assertEqual(code, 200)
        self.assertTrue(body["authenticated"])
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        session = cookie.split(";", 1)[0]
        code, session_body, _ = self.request("GET", "/api/session", headers={"Cookie": session})
        self.assertEqual(code, 200)
        self.assertEqual(session_body["csrf"], body["csrf"])
        code, _, _ = self.request("POST", "/api/logout", headers={"Cookie": session, "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(code, 403)
        code, _, _ = self.request("POST", "/api/logout", headers={"Cookie": session, "Origin": f"http://127.0.0.1:{self.port}", "X-CSRF-Token": body["csrf"]})
        self.assertEqual(code, 200)
        self.assertEqual(self.request("GET", "/api/session", headers={"Cookie": session})[0], 401)

    def test_fm1_inventory_requires_login_and_never_claims_live_identity(self):
        code, body, _ = self.request("GET", "/api/inventory")
        self.assertEqual(code, 401)
        code, body, headers = self.login()
        self.assertEqual(code, 200)
        session = headers["Set-Cookie"].split(";", 1)[0]
        code, payload, _ = self.request("GET", "/api/inventory", headers={"Cookie": session})
        self.assertEqual(code, 200)
        self.assertEqual(payload["phase"], "FM1")
        self.assertFalse(payload["live_probe_performed"])
        self.assertEqual(payload["running_firmware"]["state"], "not_probed")
        self.assertFalse(payload["capabilities"]["backup_browser"])
        self.assertFalse(payload["capabilities"]["flash"])

    def test_no_hardware_or_mutating_endpoints(self):
        code, body, headers = self.login()
        self.assertEqual(code, 200)
        session = headers["Set-Cookie"].split(";", 1)[0]
        code, status, _ = self.request("GET", "/api/status", headers={"Cookie": session})
        self.assertEqual(code, 200)
        self.assertFalse(status["hardware_access"])
        self.assertFalse(status["flash_enabled"])
        for target in ("/api/flash", "/api/restore", "/api/backup", "/api/build", "/api/shell"):
            with self.subTest(target=target):
                c, _, _ = self.request("POST", target, headers={"Cookie": session, "Origin": f"http://127.0.0.1:{self.port}"})
                self.assertEqual(c, 404)

    def test_unsafe_host_rejected(self):
        code, _, _ = self.request("GET", "/api/session", headers={"Host": "attacker.example"})
        self.assertEqual(code, 421)

    def test_login_body_size_cap_and_static_security_headers(self):
        code, _, _ = self.request("POST", "/api/login", "X" * 3000, headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(code, 400)
        code, data, headers = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"Firmware Studio", data)
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")


if __name__ == "__main__":
    unittest.main()
