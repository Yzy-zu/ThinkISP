"""
Unit tests for thinkisp.core.network module.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Ensure thinkisp package is importable regardless of CWD
THINKISP_ROOT = Path(__file__).resolve().parent.parent
if str(THINKISP_ROOT) not in sys.path:
    sys.path.insert(0, str(THINKISP_ROOT))

from thinkisp.config import HOTSPOT_CONN
ETH_IFACE, WLAN_IFACE = "upstream-test", "wifi-test"
from thinkisp.core.network import (
    CommandError,
    apply_upstream_dns,
    enable_ip_forwarding,
    format_bytes,
    format_duration,
    format_speed,
    get_current_wifi_connection,
    get_default_gateway,
    get_ethernet_status,
    get_hotspot_ip,
    get_hotspot_status,
    get_iface_bytes,
    get_interface_stats,
    get_ip_forward_status,
    get_upstream_connections,
    is_hotspot_active,
    is_ip_forwarding_enabled,
    load_state,
    notify,
    parse_duration_str,
    parse_speed_mbps,
    run_cmd,
    save_state,
)


class TestNetworkUtils(unittest.TestCase):
    """Tests for byte, speed, and time formatting and parsing utilities."""

    def test_format_bytes(self):
        """Test byte formatting across B, KB, MB, and GB boundaries."""
        self.assertEqual(format_bytes(0), "0 B")
        self.assertEqual(format_bytes(512), "512 B")
        self.assertEqual(format_bytes(1023), "1023 B")
        self.assertEqual(format_bytes(1024), "1.0 KB")
        self.assertEqual(format_bytes(1536), "1.5 KB")
        self.assertEqual(format_bytes(1024 * 1024), "1.0 MB")
        self.assertEqual(format_bytes(5 * 1024 * 1024), "5.0 MB")
        self.assertEqual(format_bytes(1024 * 1024 * 1024), "1.00 GB")
        self.assertEqual(format_bytes(int(2.5 * 1024 * 1024 * 1024)), "2.50 GB")

    def test_format_speed(self):
        """Test formatting bitrate values in bps, Kbps, Mbps, and Gbps."""
        self.assertEqual(format_speed(500), "500 bps")
        self.assertEqual(format_speed(999), "999 bps")
        self.assertEqual(format_speed(1000), "1.0 Kbps")
        self.assertEqual(format_speed(250000), "250.0 Kbps")
        self.assertEqual(format_speed(1_000_000), "1.0 Mbps")
        self.assertEqual(format_speed(867_000_000), "867.0 Mbps")
        self.assertEqual(format_speed(1_000_000_000), "1.00 Gbps")
        self.assertEqual(format_speed(2_500_000_000), "2.50 Gbps")
        self.assertEqual(format_speed("invalid"), "0 bps")

    def test_format_duration(self):
        """Test duration formatting into seconds, minutes, and hours."""
        self.assertEqual(format_duration(0), "0s")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(60), "1m 00s")
        self.assertEqual(format_duration(125), "2m 05s")
        self.assertEqual(format_duration(3600), "1h 00m 00s")
        self.assertEqual(format_duration(3665), "1h 01m 05s")
        self.assertEqual(format_duration(-10), "0s")

    def test_parse_duration_str(self):
        """Test parsing user duration strings (45m, 1h, 90, 1h30m)."""
        self.assertEqual(parse_duration_str("45m"), 2700)
        self.assertEqual(parse_duration_str("1h"), 3600)
        self.assertEqual(parse_duration_str("1h30m"), 5400)
        self.assertEqual(parse_duration_str("2h 15m 30s"), 8130)
        self.assertEqual(parse_duration_str("45s"), 45)
        # Numbers without unit default to minutes
        self.assertEqual(parse_duration_str("30"), 1800)
        self.assertEqual(parse_duration_str("   "), 0)

    def test_parse_speed_mbps(self):
        """Test parsing bandwidth strings (30m, 50mbps, 100, 500k, 1g)."""
        self.assertEqual(parse_speed_mbps("30m"), 30.0)
        self.assertEqual(parse_speed_mbps("50mbps"), 50.0)
        self.assertEqual(parse_speed_mbps("100"), 100.0)
        self.assertEqual(parse_speed_mbps("500k"), 0.5)
        self.assertEqual(parse_speed_mbps("1g"), 1000.0)
        self.assertIsNone(parse_speed_mbps("invalid"))


class TestCommandExecutionAndNotify(unittest.TestCase):
    """Tests for run_cmd and notify functions."""

    @patch("subprocess.run")
    def test_run_cmd_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="output line\n", stderr="")
        res = run_cmd(["echo", "hello"])
        self.assertEqual(res, "output line")
        self.assertEqual(mock_run.call_args.args[0], ["echo", "hello"])
        self.assertEqual(mock_run.call_args.kwargs["env"]["LC_ALL"], "C")

    @patch("subprocess.run")
    def test_run_cmd_failure_with_check(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        with self.assertRaisesRegex(CommandError, "error"):
            run_cmd(["false"], check=True)

    @patch("subprocess.run")
    def test_run_cmd_timeout(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["sleep"], timeout=5)
        res = run_cmd(["sleep", "10"])
        self.assertIsNone(res)

    @patch("subprocess.run")
    def test_run_cmd_oserror(self, mock_run):
        mock_run.side_effect = FileNotFoundError()
        res = run_cmd(["non_existent_binary"])
        self.assertIsNone(res)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_notify_with_notify_send(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/notify-send"
        notify("Test Title", "Test Message", icon="dialog-information", urgency="critical")
        mock_run.assert_called_once_with(
            ["notify-send", "-a", "ThinkISP", "-i", "dialog-information", "-u", "critical", "Test Title", "Test Message"],
            timeout=4,
            capture_output=True
        )

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_notify_missing_notify_send(self, mock_run, mock_which):
        mock_which.return_value = None
        notify("Test Title", "Test Message")
        mock_run.assert_not_called()


class TestStateManagement(unittest.TestCase):
    """Tests for load_state and save_state persistence."""

    def test_load_and_save_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_state_file = Path(tmpdir) / "sub" / "state.json"
            fake_state_dir = fake_state_file.parent

            with patch("thinkisp.core.network.STATE_FILE", fake_state_file), \
                 patch("thinkisp.core.network.STATE_DIR", fake_state_dir):
                # When file doesn't exist
                self.assertEqual(load_state(), {})

                # Save state
                data = {"gaming_mode": True, "dns": "Cloudflare DNS"}
                save_state(data)

                # Verify file was written
                self.assertTrue(fake_state_file.exists())
                loaded = load_state()
                self.assertEqual(loaded, data)


class TestNetworkOperations(unittest.TestCase):
    """Tests for gateway, hotspot status, forwarding, interface stats, and NM integration."""

    def setUp(self):
        detected = patch('thinkisp.core.network.detect_eth_iface', return_value=ETH_IFACE)
        detected.start()
        self.addCleanup(detected.stop)
        for name, value in [("ETH_IFACE", ETH_IFACE), ("WLAN_IFACE", WLAN_IFACE)]:
            selected = patch(f"thinkisp.core.network.{name}", value)
            selected.start()
            self.addCleanup(selected.stop)

    @patch("thinkisp.core.network.run_cmd")
    def test_get_default_gateway_found(self, mock_run_cmd):
        mock_run_cmd.return_value = "default via 192.168.1.1 dev enp0s31f6 proto dhcp src 192.168.1.100 metric 100"
        gw = get_default_gateway()
        self.assertEqual(gw, "192.168.1.1")
        mock_run_cmd.assert_called_with(["ip", "route", "show", "default"])

    @patch("thinkisp.core.network.run_cmd")
    def test_get_default_gateway_none(self, mock_run_cmd):
        mock_run_cmd.return_value = ""
        self.assertIsNone(get_default_gateway())

        mock_run_cmd.return_value = None
        self.assertIsNone(get_default_gateway())

    @patch("thinkisp.core.network.run_cmd")
    def test_is_hotspot_active_true(self, mock_run_cmd):
        mock_run_cmd.return_value = f"{HOTSPOT_CONN}:{WLAN_IFACE}:activated\nwired-conn:enp0s31f6:activated"
        self.assertTrue(is_hotspot_active())

    @patch("thinkisp.core.network.run_cmd")
    def test_is_hotspot_active_false(self, mock_run_cmd):
        mock_run_cmd.return_value = "home-wifi:wlan0:activated"
        self.assertFalse(is_hotspot_active())

        mock_run_cmd.return_value = None
        self.assertFalse(is_hotspot_active())

    @patch("thinkisp.core.network.is_hotspot_active")
    @patch("thinkisp.core.network.get_hotspot_ip")
    @patch("thinkisp.core.network.get_ip_forward_status")
    @patch("thinkisp.core.network.get_ethernet_status")
    def test_get_hotspot_status_active(self, mock_eth, mock_fwd, mock_ip, mock_active):
        mock_active.return_value = True
        mock_ip.return_value = "10.42.0.1/24"
        mock_fwd.return_value = True
        mock_eth.return_value = {"interface": ETH_IFACE, "connected": True, "speed": "1000Mb/s"}

        status = get_hotspot_status()
        self.assertTrue(status["active"])
        self.assertEqual(status["ssid"], HOTSPOT_CONN)
        self.assertEqual(status["ip"], "10.42.0.1/24")
        self.assertTrue(status["forwarding"])
        self.assertTrue(status["ethernet"]["connected"])

    @patch("thinkisp.core.network.is_hotspot_active")
    @patch("thinkisp.core.network.get_ip_forward_status")
    @patch("thinkisp.core.network.get_ethernet_status")
    def test_get_hotspot_status_inactive(self, mock_eth, mock_fwd, mock_active):
        mock_active.return_value = False
        mock_fwd.return_value = False
        mock_eth.return_value = {"interface": ETH_IFACE, "connected": False, "speed": "Desconocida"}

        status = get_hotspot_status()
        self.assertFalse(status["active"])
        self.assertIsNone(status["ssid"])
        self.assertIsNone(status["ip"])
        self.assertFalse(status["forwarding"])

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_is_ip_forwarding_enabled(self, mock_read, mock_exists):
        mock_exists.return_value = True
        mock_read.return_value = "1\n"
        self.assertTrue(is_ip_forwarding_enabled())
        self.assertTrue(get_ip_forward_status())

        mock_read.return_value = "0\n"
        self.assertFalse(is_ip_forwarding_enabled())

        mock_exists.return_value = False
        self.assertFalse(is_ip_forwarding_enabled())

    @patch("thinkisp.core.network.run_cmd")
    def test_enable_ip_forwarding_via_sysctl(self, mock_run_cmd):
        mock_run_cmd.return_value = "net.ipv4.ip_forward = 1"
        self.assertTrue(enable_ip_forwarding())
        mock_run_cmd.assert_called_with(["sysctl", "-w", "net.ipv4.ip_forward=1"])

    @patch("thinkisp.core.network.run_cmd")
    @patch("pathlib.Path.write_text")
    def test_enable_ip_forwarding_via_proc_fallback(self, mock_write, mock_run_cmd):
        mock_run_cmd.return_value = None
        self.assertTrue(enable_ip_forwarding())
        mock_write.assert_called_once_with("1")

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_get_interface_stats(self, mock_read, mock_exists):
        mock_exists.return_value = True
        # Side effect for rx_bytes, tx_bytes, rx_packets, tx_packets
        mock_read.side_effect = ["10485760\n", "5242880\n", "7800\n", "4500\n"]

        stats = get_interface_stats("wlan0")
        self.assertEqual(stats["interface"], "wlan0")
        self.assertEqual(stats["rx_bytes"], 10485760)
        self.assertEqual(stats["tx_bytes"], 5242880)
        self.assertEqual(stats["rx_packets"], 7800)
        self.assertEqual(stats["tx_packets"], 4500)
        self.assertEqual(stats["rx_formatted"], "10.0 MB")
        self.assertEqual(stats["tx_formatted"], "5.0 MB")

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_get_iface_bytes(self, mock_read, mock_exists):
        mock_exists.return_value = True
        mock_read.side_effect = ["1024\n", "2048\n"]
        rx, tx = get_iface_bytes("wlan0")
        self.assertEqual(rx, 1024)
        self.assertEqual(tx, 2048)

    @patch("thinkisp.core.network.run_cmd")
    def test_get_current_wifi_connection(self, mock_run_cmd):
        mock_run_cmd.return_value = (
            f"Eduroam:802-11-wireless:{WLAN_IFACE}:activated\n"
            f"Wired:802-3-ethernet:{ETH_IFACE}:activated"
        )
        self.assertEqual(get_current_wifi_connection(), "Eduroam")

    @patch("thinkisp.core.network.run_cmd")
    def test_get_upstream_connections(self, mock_run_cmd):
        mock_run_cmd.return_value = (
            f"Lab Ethernet:802-3-ethernet:{ETH_IFACE}\n"
            "Office Ethernet:802-3-ethernet:another-interface\n"
            "Hotspot:802-11-wireless:uuid3"
        )
        conns = get_upstream_connections()
        self.assertEqual(conns, ["Lab Ethernet"])

    @patch('thinkisp.core.dns_state.configure_dns', return_value=(True, 'configured'))
    def test_apply_upstream_dns_uses_recoverable_session(self, configure):
        apply_upstream_dns('1.1.1.1 1.0.0.1')
        configure.assert_called_with('cloudflare')
        apply_upstream_dns(None)
        configure.assert_called_with(None)

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    @patch("thinkisp.core.network.run_cmd")
    def test_get_ethernet_status_connected(self, mock_run_cmd, mock_read, mock_exists):
        mock_exists.return_value = True
        mock_read.side_effect = ["1\n", "up\n"]  # carrier, operstate
        mock_run_cmd.return_value = "Settings for enp0s31f6:\nSpeed: 1000Mb/s\nDuplex: Full"

        eth = get_ethernet_status()
        self.assertEqual(eth["interface"], ETH_IFACE)
        self.assertTrue(eth["connected"])
        self.assertEqual(eth["operstate"], "up")
        self.assertEqual(eth["speed"], "1000Mb/s")

    @patch("pathlib.Path.exists")
    @patch("pathlib.Path.read_text")
    def test_get_ethernet_status_disconnected(self, mock_read, mock_exists):
        mock_exists.return_value = True
        mock_read.side_effect = ["0\n", "down\n"]

        eth = get_ethernet_status()
        self.assertFalse(eth["connected"])
        self.assertEqual(eth["operstate"], "down")
        self.assertEqual(eth["speed"], "Desconocida")

    @patch("thinkisp.core.network.run_cmd")
    def test_get_hotspot_ip(self, mock_run_cmd):
        mock_run_cmd.return_value = "3: wlan0    inet 10.42.0.1/24 brd 10.42.0.255 scope global wlan0"
        self.assertEqual(get_hotspot_ip(), "10.42.0.1/24")

        mock_run_cmd.return_value = ""
        self.assertEqual(get_hotspot_ip(), "Sin datos")


if __name__ == "__main__":
    unittest.main()
