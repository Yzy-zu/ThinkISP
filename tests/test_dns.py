"""
Unit tests for thinkisp.core.dns module.
"""

from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, call, patch

# Ensure thinkisp package is importable regardless of CWD
THINKISP_ROOT = Path(__file__).resolve().parent.parent
if str(THINKISP_ROOT) not in sys.path:
    sys.path.insert(0, str(THINKISP_ROOT))

from thinkisp.config import DNS_PROVIDERS
from thinkisp.core.dns import (
    benchmark_dns,
    get_adblock_status,
    get_current_dns,
    run_speedtest,
    set_adblock,
    set_dns_provider,
    set_upstream_dns,
    test_internet_speed,
)


class TestDnsProviderConfiguration(unittest.TestCase):
    """Tests for setting and querying upstream DNS and AdBlock providers."""

    @patch("thinkisp.core.dns.notify")
    @patch("thinkisp.core.dns.save_state")
    @patch("thinkisp.core.dns.load_state", return_value={})
    @patch("thinkisp.core.dns.apply_upstream_dns")
    def test_set_upstream_dns_cloudflare(self, mock_apply, mock_load, mock_save, mock_notify):
        success, msg = set_upstream_dns("cloudflare")
        self.assertTrue(success)
        self.assertIn("Cloudflare DNS", msg)
        mock_apply.assert_called_once_with("1.1.1.1 1.0.0.1")
        mock_save.assert_called_once_with({"dns": "Cloudflare DNS", "adblock": False})
        mock_notify.assert_called_once()

    @patch("thinkisp.core.dns.notify")
    @patch("thinkisp.core.dns.save_state")
    @patch("thinkisp.core.dns.load_state", return_value={})
    @patch("thinkisp.core.dns.apply_upstream_dns")
    def test_set_upstream_dns_adguard(self, mock_apply, mock_load, mock_save, mock_notify):
        success, msg = set_upstream_dns("adguard")
        self.assertTrue(success)
        self.assertIn("AdGuard DNS", msg)
        mock_apply.assert_called_once_with("94.140.14.14 94.140.15.15")
        mock_save.assert_called_once_with({"dns": "AdGuard DNS (Anti-Anuncios)", "adblock": True})

    @patch("thinkisp.core.dns.notify")
    @patch("thinkisp.core.dns.save_state")
    @patch("thinkisp.core.dns.load_state", return_value={"dns": "Cloudflare DNS", "adblock": True})
    @patch("thinkisp.core.dns.apply_upstream_dns")
    def test_set_upstream_dns_auto(self, mock_apply, mock_load, mock_save, mock_notify):
        success, msg = set_upstream_dns("auto")
        self.assertTrue(success)
        self.assertIn("automático", msg)
        mock_apply.assert_called_once_with(None)
        mock_save.assert_called_once_with({"dns": "Auto / DHCP", "adblock": False})

    def test_set_upstream_dns_unknown(self):
        success, msg = set_upstream_dns("unsupported_dns")
        self.assertFalse(success)
        self.assertIn("Proveedor desconocido", msg)

    @patch("thinkisp.core.dns.load_state")
    def test_get_current_dns(self, mock_load):
        mock_load.return_value = {"dns": "Google Public DNS"}
        self.assertEqual(get_current_dns(), "Google Public DNS")

        mock_load.return_value = {}
        self.assertEqual(get_current_dns(), "Auto / DHCP")

    @patch("thinkisp.core.dns.set_dns_provider")
    def test_set_adblock(self, mock_set_dns):
        set_adblock(True)
        mock_set_dns.assert_called_with("adguard")

        set_adblock(False)
        mock_set_dns.assert_called_with("auto")

    @patch("thinkisp.core.dns.load_state")
    def test_get_adblock_status(self, mock_load):
        mock_load.return_value = {"adblock": True}
        self.assertTrue(get_adblock_status())

        mock_load.return_value = {}
        self.assertFalse(get_adblock_status())


class TestDnsBenchmark(unittest.TestCase):
    """Tests for DNS latency benchmarking."""

    @patch("thinkisp.core.dns.run_cmd")
    def test_benchmark_dns(self, mock_run_cmd):
        def fake_dig(cmd):
            ip = cmd[1].replace("@", "")
            if ip == "1.1.1.1":
                return ";; Query time: 10 msec\n;; SERVER: 1.1.1.1#53"
            elif ip == "8.8.8.8":
                return ";; Query time: 22 msec\n;; SERVER: 8.8.8.8#53"
            elif ip == "9.9.9.9":
                return ";; Query time: 16 msec\n;; SERVER: 9.9.9.9#53"
            elif ip == "94.140.14.14":
                return ";; Query time: 30 msec\n;; SERVER: 94.140.14.14#53"
            else:
                return None  # Timeout for OpenDNS

        mock_run_cmd.side_effect = fake_dig
        results = benchmark_dns()

        self.assertEqual(len(results), len(DNS_PROVIDERS))
        # First must be the fastest (Cloudflare 10ms)
        self.assertEqual(results[0]["key"], "cloudflare")
        self.assertEqual(results[0]["time"], 10)

        # Second fastest (Quad9 16ms)
        self.assertEqual(results[1]["key"], "quad9")
        self.assertEqual(results[1]["time"], 16)

        # Last must be the timed out provider (OpenDNS)
        self.assertEqual(results[-1]["key"], "opendns")
        self.assertIsNone(results[-1]["time"])


class TestInternetSpeed(unittest.TestCase):
    """Tests for internet speed test against Cloudflare edge CDN."""

    @patch("subprocess.run")
    def test_speedtest_success(self, mock_run):
        # 1. Ping response (0.014 seconds -> 14.0 ms)
        res_ping = MagicMock(stdout="0.014000\n", returncode=0)
        # 2. Download response (2.0 seconds for 25,000,000 bytes -> 100 Mbps)
        res_down = MagicMock(stdout="2.000000 25000000\n", returncode=0)
        # 3. Upload response (1.0 seconds for 5,000,000 bytes -> 40 Mbps)
        res_up = MagicMock(stdout="1.000000 5000000\n", returncode=0)

        mock_run.side_effect = [res_ping, res_down, res_up]

        metrics = test_internet_speed()
        self.assertAlmostEqual(metrics["ping_ms"], 14.0, places=1)
        self.assertAlmostEqual(metrics["down_mbps"], 100.0, places=1)
        self.assertAlmostEqual(metrics["up_mbps"], 40.0, places=1)
        self.assertIsNone(metrics["error"])

        # Check that test_internet_speed and run_speedtest are identical
        self.assertEqual(mock_run.call_count, 3)

    @patch("subprocess.run")
    def test_speedtest_error_handling(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["curl"], timeout=6)
        metrics = run_speedtest()
        self.assertIsNone(metrics["ping_ms"])
        self.assertIsNotNone(metrics["error"])
        self.assertIn("timed out", metrics["error"])


if __name__ == "__main__":
    unittest.main()
