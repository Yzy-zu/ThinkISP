"""
Unit and integration tests for thinkISP Captive Portal HTTP Server and page renderer.
Zero external dependencies, stdlib only. Private loopback tests only.
"""

import builtins
import http.client
import io
import os
import re
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

from thinkisp.errors import ThinkISPError
from thinkisp.portal import make_server, render_page
from thinkisp.portal.http import (
    CaptivePortalHandler,
    CaptivePortalServer,
    HOST_REGEX,
    MAX_FORM_SIZE,
    MAX_TOKEN_LENGTH,
    PORTAL_PATHS,
    TOKEN_REGEX,
)


class FakeBrokerBackend:
    """Synchronous JSON-compatible test broker backend."""

    def __init__(self):
        self.authorized_ips = set()
        self.challenges = {}  # ip -> token
        self.used_tokens = set()
        self.history = []
        self.downtime = False

    def __call__(self, operation: str, payload: dict) -> dict:
        self.history.append((operation, payload))
        if self.downtime:
            raise ThinkISPError("Broker connection failure / downtime")

        ip = payload.get("ip")
        if operation == "client_status":
            return {"authorized": ip in self.authorized_ips}
        elif operation == "challenge":
            token = f"challenge-token-{len(self.history)}"
            self.challenges[ip] = token
            return {"token": token}
        elif operation == "accept":
            token = payload.get("token")
            if token in self.used_tokens:
                raise ThinkISPError("Token ya utilizado")
            if self.challenges.get(ip) == token:
                self.used_tokens.add(token)
                self.authorized_ips.add(ip)
                return {"authorized": True}
            raise ThinkISPError("Token no válido o caducado")
        else:
            raise ThinkISPError(f"Operación no soportada: {operation}")


class TestPortalPureHelpers(unittest.TestCase):
    """Test pure functions such as render_page and regexes."""

    def test_render_page_utf8_bytes(self):
        res = render_page("Mi Red", "token123", "/accept", authorized=False)
        self.assertIsInstance(res, bytes)
        decoded = res.decode("utf-8")
        self.assertIn("<!DOCTYPE html>", decoded)
        self.assertIn('<html lang="es">', decoded)
        self.assertIn("Mi Red &bull; thinkISP", decoded)
        self.assertIn("Continuar", decoded)
        self.assertIn('name="token" value="token123"', decoded)
        self.assertIn('action="/accept"', decoded)

    def test_render_page_escaping_malicious_label(self):
        malicious = '<script>alert("xss")</script><img src=x onerror=evil()>"\'&'
        res = render_page(malicious, "tok", "/accept")
        decoded = res.decode("utf-8")
        self.assertNotIn("<script>", decoded)
        self.assertNotIn("<img", decoded)
        self.assertIn("&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;", decoded)

    def test_render_page_authorized_state(self):
        res = render_page("Mi Red", None, "/accept", authorized=True)
        decoded = res.decode("utf-8")
        self.assertIn("Conexión habilitada", decoded)
        self.assertNotIn('<form', decoded)
        self.assertNotIn('name="token"', decoded)

    def test_render_page_error_state(self):
        res = render_page("Mi Red", None, "/accept", authorized=False, error="Token caducado")
        decoded = res.decode("utf-8")
        self.assertIn("Token caducado", decoded)
        self.assertIn("Aviso del portal", decoded)

    def test_token_regex(self):
        self.assertTrue(TOKEN_REGEX.match("abc-DEF_123.~"))
        self.assertFalse(TOKEN_REGEX.match("bad token"))
        self.assertFalse(TOKEN_REGEX.match("<script>"))
        self.assertFalse(TOKEN_REGEX.match("token;drop"))
        self.assertFalse(TOKEN_REGEX.match(""))


class TestPortalHTTPServer(unittest.TestCase):
    """Integration and HTTP protocol tests using local loopback socket."""

    def setUp(self):
        self.backend = FakeBrokerBackend()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.port = self.listener.getsockname()[1]
        self.gateway = "127.0.0.1"
        self.label = "Test Portal WiFi"
        self.server = make_server(
            listener=self.listener,
            gateway=self.gateway,
            port=self.port,
            label=self.label,
            backend=self.backend,
            max_workers=4,
        )
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2.0)

    def _http_request(self, method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5.0)
        req_headers = headers or {}
        conn.request(method, path, body=body, headers=req_headers)
        response = conn.getresponse()
        resp_body = response.read()
        conn.close()
        return response, resp_body

    def _raw_socket_request(self, raw_bytes):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect(("127.0.0.1", self.port))
        s.sendall(raw_bytes)
        chunks = []
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except (socket.timeout, ConnectionResetError):
            pass
        finally:
            s.close()
        return b"".join(chunks)

    def test_canonical_redirect_from_arbitrary_detection_hosts(self):
        """GET with intercepted external Host must 303 to canonical portal URL."""
        test_hosts = [
            ("connectivitycheck.gstatic.com", "/generate_204"),
            ("captive.apple.com", "/hotspot-detect.html"),
            ("www.msftconnecttest.com", "/connecttest.txt"),
            ("www.msftncsi.com", "/ncsi.txt"),
            ("detectportal.firefox.com", "/canonical.html"),
            ("example.com", "/"),
            ("1.1.1.1", "/some/path"),
        ]
        for host, path in test_hosts:
            resp, _ = self._http_request(
                "GET",
                path,
                headers={"Host": host},
            )
            self.assertEqual(resp.status, 303, f"Failed for host: {host}")
            expected_location = f"http://127.0.0.1:{self.port}/"
            self.assertEqual(resp.headers.get("Location"), expected_location)
            # Ensure host is NEVER reflected
            self.assertNotIn(host, resp.headers.get("Location"))

    def test_no_open_redirect(self):
        """Verify server never redirects to user-specified external hosts."""
        evil_hosts = ["evil.com", "attacker.net:8080", "127.0.0.1.attacker.com"]
        for host in evil_hosts:
            resp, _ = self._http_request("GET", f"http://{host}/", headers={"Host": host})
            self.assertEqual(resp.status, 303)
            self.assertEqual(resp.headers.get("Location"), f"http://127.0.0.1:{self.port}/")

    def test_canonical_get_unauthorized_serves_form(self):
        """Canonical GET queries client_status + challenge, then serves HTML form."""
        resp, body = self._http_request(
            "GET",
            "/",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        self.assertEqual(resp.status, 200)
        decoded = body.decode("utf-8")
        self.assertIn("Test Portal WiFi &bull; thinkISP", decoded)
        self.assertIn('action="http://127.0.0.1:' + str(self.port) + '/accept"', decoded)
        self.assertIn('name="token"', decoded)
        self.assertIn("Continuar", decoded)

        # Security headers
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store, no-cache, must-revalidate")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertIn("default-src 'none'", resp.headers.get("Content-Security-Policy", ""))
        self.assertEqual(resp.headers.get("Referrer-Policy"), "no-referrer")
        self.assertIsNone(resp.headers.get("Set-Cookie"))

        # Verify backend calls
        self.assertEqual(self.backend.history[0][0], "client_status")
        self.assertEqual(self.backend.history[0][1], {"ip": "127.0.0.1"})
        self.assertEqual(self.backend.history[1][0], "challenge")
        self.assertEqual(self.backend.history[1][1], {"ip": "127.0.0.1"})

    def test_peer_ip_cannot_be_supplied_or_overridden(self):
        """Ensure peer IP is derived strictly from socket and ignores headers/queries."""
        resp, _ = self._http_request(
            "GET",
            "/?ip=203.0.113.5&mac=00:11:22:33:44:55",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "X-Forwarded-For": "203.0.113.199",
                "X-Real-IP": "203.0.113.199",
                "Forwarded": "for=203.0.113.199",
                "Cookie": "ip=203.0.113.199; session=admin",
            },
        )
        self.assertEqual(resp.status, 200)
        for op, payload in self.backend.history:
            self.assertEqual(payload.get("ip"), "127.0.0.1")
            self.assertNotEqual(payload.get("ip"), "203.0.113.199")
            self.assertNotEqual(payload.get("ip"), "203.0.113.5")

    def test_valid_nonce_post_and_authorized_page(self):
        """Submit valid nonce, receive 303 canonical redirect, then view authorized page."""
        # 1. Get challenge token
        resp, body = self._http_request(
            "GET",
            "/",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        self.assertEqual(resp.status, 200)
        match = re.search(r'name="token" value="([^"]+)"', body.decode("utf-8"))
        self.assertTrue(match)
        token = match.group(1)

        # 2. POST /accept
        post_data = f"token={token}".encode("utf-8")
        resp, _ = self._http_request(
            "POST",
            "/accept",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": f"http://127.0.0.1:{self.port}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(post_data)),
            },
            body=post_data,
        )
        self.assertEqual(resp.status, 303)
        self.assertEqual(resp.headers.get("Location"), f"http://127.0.0.1:{self.port}/")
        self.assertIsNone(resp.headers.get("Set-Cookie"))

        # 3. Subsequent GET / shows authorized status page without form or challenge
        history_len_before = len(self.backend.history)
        resp2, body2 = self._http_request(
            "GET",
            "/",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        self.assertEqual(resp2.status, 200)
        decoded2 = body2.decode("utf-8")
        self.assertIn("Conexión habilitada", decoded2)
        self.assertNotIn('<form', decoded2)
        self.assertNotIn('name="token"', decoded2)
        # Verify challenge was NOT called on authorized GET
        new_calls = self.backend.history[history_len_before:]
        self.assertEqual(len(new_calls), 1)
        self.assertEqual(new_calls[0][0], "client_status")

    def test_nonce_failure_and_replay(self):
        """Replaying a token or sending an invalid token must return 403."""
        # First obtain a token
        resp, body = self._http_request(
            "GET",
            "/",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        token = re.search(r'name="token" value="([^"]+)"', body.decode("utf-8")).group(1)

        # Submit valid token first time
        post_data = f"token={token}".encode("utf-8")
        resp1, _ = self._http_request(
            "POST",
            "/accept",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(post_data)),
            },
            body=post_data,
        )
        self.assertEqual(resp1.status, 303)

        # Replay the same token
        resp2, body2 = self._http_request(
            "POST",
            "/accept",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(post_data)),
            },
            body=post_data,
        )
        self.assertEqual(resp2.status, 403)
        self.assertIn("Autorización denegada", body2.decode("utf-8"))

        # Submit fake token
        fake_data = b"token=completely-invalid-nonce"
        resp3, _ = self._http_request(
            "POST",
            "/accept",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(fake_data)),
            },
            body=fake_data,
        )
        self.assertEqual(resp3.status, 403)

    def test_foreign_origin_rejected(self):
        """POST with foreign Origin must be rejected with 403."""
        post_data = b"token=testtoken"
        resp, _ = self._http_request(
            "POST",
            "/accept",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Origin": "http://evil-attacker.com",
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(post_data)),
            },
            body=post_data,
        )
        self.assertEqual(resp.status, 403)

    def test_content_type_validation(self):
        """POST with JSON or invalid content-type must be rejected with 415."""
        post_data = b'{"token": "xyz"}'
        resp, _ = self._http_request(
            "POST",
            "/accept",
            headers={
                "Host": f"127.0.0.1:{self.port}",
                "Content-Type": "application/json",
                "Content-Length": str(len(post_data)),
            },
            body=post_data,
        )
        self.assertEqual(resp.status, 415)

    def test_framing_and_content_length_validation(self):
        """Test rejection of chunked transfer, duplicate/malformed Content-Length."""
        # 1. Chunked transfer encoding
        raw_chunked = (
            f"POST /accept HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Transfer-Encoding: chunked\r\n\r\n"
            f"5\r\ntoken\r\n0\r\n\r\n"
        ).encode("ascii")
        res = self._raw_socket_request(raw_chunked)
        self.assertIn(b"400 Bad Request", res)

        # 2. Duplicate Content-Length
        raw_dup_cl = (
            f"POST /accept HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: 10\r\n"
            f"Content-Length: 10\r\n\r\n"
            f"token=test"
        ).encode("ascii")
        res2 = self._raw_socket_request(raw_dup_cl)
        self.assertIn(b"400 Bad Request", res2)

        # 3. Non-numeric Content-Length
        raw_bad_cl = (
            f"POST /accept HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: invalid\r\n\r\n"
            f"token=test"
        ).encode("ascii")
        res3 = self._raw_socket_request(raw_bad_cl)
        self.assertIn(b"400 Bad Request", res3)

    def test_duplicate_origin_and_content_type_fail_closed(self):
        for header in ('Origin', 'Content-Type'):
            value = f'http://127.0.0.1:{self.port}' if header == 'Origin' else 'application/x-www-form-urlencoded'
            headers = f'{header}: {value}\r\n' * 2
            if header == 'Origin':
                headers += 'Content-Type: application/x-www-form-urlencoded\r\n'
            raw = (f'POST /accept HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n'
                   f'{headers}Content-Length: 9\r\n\r\ntoken=abc').encode()
            with self.subTest(header=header):
                self.assertIn(b'400', self._raw_socket_request(raw))

    def test_malformed_absolute_url_does_not_kill_server(self):
        raw = (f'GET http://[invalid/ HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n\r\n').encode()
        self.assertIn(b'400', self._raw_socket_request(raw))
        response, _ = self._http_request('GET', '/', headers={'Host':f'127.0.0.1:{self.port}'})
        self.assertEqual(response.status, 200)

    def test_huge_body_rejected_before_reading(self):
        """Oversized body (> 2048 bytes) must be rejected with 413."""
        raw_huge = (
            f"POST /accept HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"Content-Type: application/x-www-form-urlencoded\r\n"
            f"Content-Length: 5000\r\n\r\n"
        ).encode("ascii") + b"A" * 100
        res = self._raw_socket_request(raw_huge)
        self.assertIn(b"413", res)

    def test_form_validation_edge_cases(self):
        """Form must have exactly one non-empty 'token' field within 128 URLsafe chars."""
        cases = [
            b"token=",  # empty token
            b"token=" + b"a" * 129,  # too long
            b"token=invalid token with spaces",  # invalid chars
            b"token=abc&extra=123",  # extra field
            b"token=abc&token=def",  # duplicate token
            b"other=abc",  # missing token field
        ]
        for body in cases:
            resp, _ = self._http_request(
                "POST",
                "/accept",
                headers={
                    "Host": f"127.0.0.1:{self.port}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Content-Length": str(len(body)),
                },
                body=body,
            )
            self.assertEqual(resp.status, 400, f"Expected 400 for body: {body}")

    def test_invalid_and_unknown_methods(self):
        """PUT, DELETE, OPTIONS, PATCH must be rejected with 405; no auth changes."""
        for method in ("PUT", "DELETE", "OPTIONS", "PATCH"):
            resp, _ = self._http_request(
                method,
                "/accept",
                headers={"Host": f"127.0.0.1:{self.port}"},
            )
            self.assertEqual(resp.status, 405, f"Expected 405 for {method}")
            self.assertIn("GET, HEAD, POST", resp.headers.get("Allow", ""))

    def test_head_method_safe(self):
        """HEAD must be safe: 200 OK headers, no body, no state/token mutation."""
        calls_before = len(self.backend.history)
        resp, body = self._http_request(
            "HEAD",
            "/",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(body), 0)
        # Ensure 'challenge' was NOT invoked during HEAD
        invoked_ops = [op for op, _ in self.backend.history[calls_before:]]
        self.assertNotIn("challenge", invoked_ops)

    def test_no_file_reads_or_path_traversal(self):
        """Unknown/traversal paths must 303 to canonical URL without reading disk."""
        traversal_paths = [
            "/etc/passwd",
            "/../../../../etc/passwd",
            "/proc/version",
            "/thinkisp/config.py",
        ]
        with patch.object(builtins, "open", wraps=builtins.open) as mock_open:
            for path in traversal_paths:
                resp, _ = self._http_request(
                    "GET",
                    path,
                    headers={"Host": f"127.0.0.1:{self.port}"},
                )
                self.assertEqual(resp.status, 303)
                self.assertEqual(resp.headers.get("Location"), f"http://127.0.0.1:{self.port}/")

            # Check that open was never called with any traversal target
            for call in mock_open.call_args_list:
                arg = str(call[0][0])
                for p in traversal_paths:
                    self.assertNotIn(p, arg)

    def test_no_subprocess_calls(self):
        """HTTP portal must never invoke subprocess or shell."""
        with patch("subprocess.Popen") as mock_popen, \
             patch("subprocess.run") as mock_run, \
             patch("os.system") as mock_system:
            resp, _ = self._http_request(
                "GET",
                "/",
                headers={"Host": f"127.0.0.1:{self.port}"},
            )
            self.assertEqual(resp.status, 200)
            mock_popen.assert_not_called()
            mock_run.assert_not_called()
            mock_system.assert_not_called()

    def test_backend_downtime(self):
        """Backend downtime must return 503 without crashing or leaking."""
        self.backend.downtime = True
        resp, body = self._http_request(
            "GET",
            "/",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        self.assertEqual(resp.status, 503)
        self.assertIn("no disponible", body.decode("utf-8"))
        self.assertNotIn("Traceback", body.decode("utf-8"))

    def test_host_header_validation_errors(self):
        """Malformed, missing or duplicate Host header must return 400."""
        # 1. Missing Host header in raw HTTP/1.1
        raw_missing = b"GET / HTTP/1.1\r\n\r\n"
        res1 = self._raw_socket_request(raw_missing)
        self.assertIn(b"400", res1)

        # 2. Duplicate Host header
        raw_dup = (
            f"GET / HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n\r\n"
        ).encode("ascii")
        res2 = self._raw_socket_request(raw_dup)
        self.assertIn(b"400", res2)

        # 3. Malformed Host header (multiple colons)
        raw_malformed = (
            f"GET / HTTP/1.1\r\n"
            f"Host: 127.0.0.1:80:80\r\n\r\n"
        ).encode("ascii")
        res3 = self._raw_socket_request(raw_malformed)
        self.assertIn(b"400", res3)


class TestMakeServerValidation(unittest.TestCase):
    """Verify input validation on make_server arguments."""

    def test_invalid_gateway(self):
        dummy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with self.assertRaises(ValueError):
            make_server(dummy_sock, "invalid-ip", 8080, "Portal", lambda op, p: {})
        with self.assertRaises(ValueError):
            make_server(dummy_sock, "256.0.0.1", 8080, "Portal", lambda op, p: {})
        with self.assertRaises(ValueError):
            make_server(dummy_sock, "::1", 8080, "Portal", lambda op, p: {})
        dummy_sock.close()

    def test_invalid_port(self):
        dummy_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with self.assertRaises(ValueError):
            make_server(dummy_sock, "127.0.0.1", 0, "Portal", lambda op, p: {})
        with self.assertRaises(ValueError):
            make_server(dummy_sock, "127.0.0.1", 65536, "Portal", lambda op, p: {})
        with self.assertRaises(ValueError):
            make_server(dummy_sock, "127.0.0.1", "bad-port", "Portal", lambda op, p: {})
        dummy_sock.close()


if __name__ == "__main__":
    unittest.main()
