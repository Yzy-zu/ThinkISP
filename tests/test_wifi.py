"""
Unit tests for thinkisp.core.wifi module.
"""

from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

# Ensure thinkisp package is importable regardless of CWD
THINKISP_ROOT = Path(__file__).resolve().parent.parent
if str(THINKISP_ROOT) not in sys.path:
    sys.path.insert(0, str(THINKISP_ROOT))

from thinkisp.config import DEFAULT_PSK, DEFAULT_SSID, HOTSPOT_CONN, WLAN_IFACE
from thinkisp.core.wifi import (
    apply_zero_latency_optimizations,
    format_signal_bar,
    get_arp_map,
    get_blocked_clients,
    get_connected_clients,
    get_hostname_for_ip,
    get_qr_ascii,
    get_stealth_mode,
    kick_client,
    optimize_low_latency,
    parse_dhcp_leases,
    set_band,
    set_stealth_mode,
    start_hotspot,
    stop_hotspot,
    unkick_client,
)


class TestHotspotLifecycle(unittest.TestCase):
    """Tests for starting and stopping the Wi-Fi hotspot."""

    @patch("thinkisp.core.wifi.is_hotspot_active")
    def test_start_hotspot_already_active(self, mock_active):
        mock_active.return_value = True
        success, msg = start_hotspot()
        self.assertTrue(success)
        self.assertIn("ya está activo", msg)

    @patch("thinkisp.core.wifi.notify")
    @patch("thinkisp.core.wifi.save_state")
    @patch("thinkisp.core.wifi.load_state")
    @patch("thinkisp.core.wifi.get_iface_bytes")
    @patch("thinkisp.core.wifi.get_current_wifi_connection", return_value="Eduroam")
    @patch("thinkisp.core.wifi.apply_zero_latency_optimizations")
    @patch("thinkisp.core.wifi.run_cmd")
    @patch("thinkisp.core.wifi.is_hotspot_active")
    def test_start_hotspot_success(
        self, mock_active, mock_run_cmd, mock_opt, mock_prev_wifi, mock_bytes, mock_load, mock_save, mock_notify
    ):
        # First call False (pre-check), second call True (post nmcli up check)
        mock_active.side_effect = [False, True]
        mock_run_cmd.return_value = "Connection successfully activated"
        mock_bytes.return_value = (1000, 2000)
        mock_load.return_value = {}

        success, msg = start_hotspot()
        self.assertTrue(success)
        self.assertIn("activada con éxito", msg)
        mock_run_cmd.assert_called_with(["nmcli", "connection", "up", HOTSPOT_CONN])
        mock_opt.assert_called_once()
        mock_save.assert_called_once()
        saved_state = mock_save.call_args[0][0]
        self.assertIn("session", saved_state)
        self.assertEqual(saved_state.get("last_wifi"), "Eduroam")
        self.assertEqual(saved_state["session"]["start_rx"], 1000)
        self.assertEqual(saved_state["session"]["start_tx"], 2000)
        mock_notify.assert_called_once()

    @patch("thinkisp.core.wifi.run_cmd")
    @patch("thinkisp.core.wifi.is_hotspot_active")
    def test_start_hotspot_failure(self, mock_active, mock_run_cmd):
        mock_active.return_value = False
        mock_run_cmd.return_value = "Error: Connection activation failed"

        success, msg = start_hotspot()
        self.assertFalse(success)
        self.assertIn("Error al activar", msg)

    @patch("thinkisp.core.wifi.is_hotspot_active")
    def test_stop_hotspot_already_inactive(self, mock_active):
        mock_active.return_value = False
        success, summary = stop_hotspot()
        self.assertTrue(success)
        self.assertEqual(summary, "La zona Wi-Fi ya estaba inactiva.")

    @patch("thinkisp.core.wifi.notify")
    @patch("thinkisp.core.wifi.save_state")
    @patch("thinkisp.core.wifi.load_state")
    @patch("thinkisp.core.wifi.get_iface_bytes")
    @patch("thinkisp.core.wifi.run_cmd")
    @patch("thinkisp.core.wifi.is_hotspot_active")
    def test_stop_hotspot_success_with_session(
        self, mock_active, mock_run_cmd, mock_bytes, mock_load, mock_save, mock_notify
    ):
        mock_active.return_value = True
        mock_load.return_value = {
            "session": {"start_time": 1000.0, "start_rx": 5000, "start_tx": 8000},
            "last_wifi": "Home-Network",
        }
        mock_bytes.return_value = (15000, 28000)  # uploaded = 10000, downloaded = 20000

        with patch("time.time", return_value=1120.0):
            success, summary = stop_hotspot()

        self.assertTrue(success)
        self.assertIsInstance(summary, dict)
        self.assertEqual(summary["duration"], 120.0)
        self.assertEqual(summary["downloaded"], 20000)
        self.assertEqual(summary["uploaded"], 10000)

        # Asserts connection down called
        mock_run_cmd.assert_any_call(["nmcli", "connection", "down", HOTSPOT_CONN])
        # Asserts previous wifi reconnected
        mock_run_cmd.assert_any_call(["nmcli", "connection", "up", "Home-Network"])
        mock_notify.assert_called_once()


class TestWifiOptimizations(unittest.TestCase):
    """Tests for low-latency tuning and QR generation."""

    @patch("thinkisp.core.wifi.run_cmd")
    def test_optimize_low_latency(self, mock_run_cmd):
        optimize_low_latency()
        mock_run_cmd.assert_any_call(["iw", "dev", WLAN_IFACE, "set", "power_save", "off"])
        mock_run_cmd.assert_any_call(["ip", "link", "set", "dev", WLAN_IFACE, "txqueuelen", "2000"])

    @patch("shutil.which")
    @patch("thinkisp.core.wifi.run_cmd")
    def test_get_qr_ascii_with_qrencode(self, mock_run_cmd, mock_which):
        mock_which.return_value = "/usr/bin/qrencode"
        mock_run_cmd.return_value = "████  ████\n██  ██  ██"
        qr = get_qr_ascii("test_ssid", "test_psk")
        self.assertEqual(qr, "████  ████\n██  ██  ██")
        mock_run_cmd.assert_called_with(["qrencode", "-t", "UTF8", "WIFI:S:test_ssid;T:WPA;P:test_psk;;"])

    @patch("shutil.which")
    def test_get_qr_ascii_fallback(self, mock_which):
        mock_which.return_value = None
        qr = get_qr_ascii("sputnik", "123456789z")
        self.assertIn("sputnik", qr)
        self.assertIn("123456789z", qr)


class TestClientInspection(unittest.TestCase):
    """Tests for connected clients parsing station dumps and DHCP leases."""

    def test_parse_dhcp_leases(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            f.write("1700000000 00:11:22:33:44:55 10.42.0.50 Galaxy-S22 01:00:11:22:33:44:55\n")
            f.write("1700000050 aa:bb:cc:dd:ee:ff 10.42.0.51 * 01:aa:bb:cc:dd:ee:ff\n")
            f_path = f.name

        try:
            leases = parse_dhcp_leases(f_path)
            self.assertIn("00:11:22:33:44:55", leases)
            self.assertEqual(leases["00:11:22:33:44:55"]["ip"], "10.42.0.50")
            self.assertEqual(leases["00:11:22:33:44:55"]["hostname"], "Galaxy-S22")

            self.assertIn("aa:bb:cc:dd:ee:ff", leases)
            self.assertEqual(leases["aa:bb:cc:dd:ee:ff"]["ip"], "10.42.0.51")
            self.assertEqual(leases["aa:bb:cc:dd:ee:ff"]["hostname"], "Dispositivo móvil")
        finally:
            Path(f_path).unlink(missing_ok=True)

    @patch("socket.gethostbyaddr")
    def test_get_hostname_for_ip_success(self, mock_dns):
        mock_dns.return_value = ("pixel-device.lan", [], ["10.42.0.12"])
        self.assertEqual(get_hostname_for_ip("10.42.0.12"), "pixel-device.lan")

    @patch("socket.gethostbyaddr")
    def test_get_hostname_for_ip_fallback(self, mock_dns):
        mock_dns.side_effect = socket.herror(1, "Unknown host")
        self.assertEqual(get_hostname_for_ip("10.42.0.99"), "Dispositivo móvil")
        self.assertEqual(get_hostname_for_ip(""), "Dispositivo móvil")
        self.assertEqual(get_hostname_for_ip("Desconocida"), "Dispositivo móvil")

    @patch("thinkisp.core.wifi.run_cmd")
    def test_get_arp_map(self, mock_run_cmd):
        mock_proc_arp = (
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            f"10.42.0.2        0x1         0x2         00:aa:bb:cc:dd:01     *        {WLAN_IFACE}\n"
            "192.168.1.1      0x1         0x2         00:aa:bb:cc:dd:02     *        enp0s31f6\n"
        )
        mock_run_cmd.return_value = f"10.42.0.3 dev {WLAN_IFACE} lladdr 00:aa:bb:cc:dd:03 REACHABLE"

        with patch("builtins.open", unittest.mock.mock_open(read_data=mock_proc_arp)):
            arp = get_arp_map()

        self.assertEqual(arp.get("00:aa:bb:cc:dd:01"), "10.42.0.2")
        self.assertEqual(arp.get("00:aa:bb:cc:dd:03"), "10.42.0.3")
        self.assertNotIn("00:aa:bb:cc:dd:02", arp)

    @patch("thinkisp.core.wifi.parse_dhcp_leases")
    @patch("thinkisp.core.wifi.get_arp_map")
    @patch("thinkisp.core.wifi.run_cmd")
    def test_get_connected_clients(self, mock_run_cmd, mock_arp, mock_leases):
        mock_station_dump = (
            f"Station 00:11:22:33:44:55 (on {WLAN_IFACE})\n"
            "    inactive time:  120 ms\n"
            "    rx bytes:       1048576\n"
            "    tx bytes:       2097152\n"
            "    signal:         -48 dBm\n"
            "    tx bitrate:     866.7 MBit/s VHT-MCS 9 80MHz\n"
            "    rx bitrate:     433.3 MBit/s VHT-MCS 7 80MHz\n"
            "    connected time: 350 seconds\n"
            f"Station aa:bb:cc:dd:ee:ff (on {WLAN_IFACE})\n"
            "    signal:         -72 dBm\n"
            "    tx bitrate:     144.0 MBit/s\n"
            "    rx bitrate:     72.0 MBit/s\n"
            "    connected time: 80 seconds\n"
        )
        mock_run_cmd.return_value = mock_station_dump
        mock_leases.return_value = {
            "00:11:22:33:44:55": {"ip": "10.42.0.10", "hostname": "Pixel-8-Pro"}
        }
        mock_arp.return_value = {
            "aa:bb:cc:dd:ee:ff": "10.42.0.25"
        }

        with patch("thinkisp.core.wifi.get_hostname_for_ip", return_value="ThinkPad-E14"):
            clients = get_connected_clients()

        self.assertEqual(len(clients), 2)
        c1 = next(c for c in clients if c["mac"] == "00:11:22:33:44:55")
        self.assertEqual(c1["ip"], "10.42.0.10")
        self.assertEqual(c1["hostname"], "Pixel-8-Pro")
        self.assertEqual(c1["signal"], -48)
        self.assertEqual(c1["tx_mbps"], 866.7)
        self.assertEqual(c1["rx_mbps"], 433.3)
        self.assertEqual(c1["conn_time"], 350)
        self.assertEqual(c1["rx_bytes"], 1048576)
        self.assertEqual(c1["tx_bytes"], 2097152)

        c2 = next(c for c in clients if c["mac"] == "aa:bb:cc:dd:ee:ff")
        self.assertEqual(c2["ip"], "10.42.0.25")
        self.assertEqual(c2["hostname"], "ThinkPad-E14")
        self.assertEqual(c2["signal"], -72)


class TestClientAccessControl(unittest.TestCase):
    """Tests for kick, unkick, denylist, stealth mode, and bands."""

    @patch("thinkisp.core.wifi.notify")
    @patch("thinkisp.core.wifi.apply_zero_latency_optimizations")
    @patch("thinkisp.core.wifi.is_hotspot_active", return_value=True)
    @patch("thinkisp.core.wifi.run_cmd")
    def test_kick_client_by_mac(self, mock_run_cmd, mock_active, mock_opt, mock_notify):
        success, msg = kick_client("00:11:22:33:44:55")
        self.assertTrue(success)
        self.assertIn("expulsado y bloqueado", msg)
        mock_run_cmd.assert_any_call([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "+802-11-wireless.mac-address-denylist", "00:11:22:33:44:55"
        ])
        mock_run_cmd.assert_any_call(["nmcli", "connection", "up", HOTSPOT_CONN])
        mock_opt.assert_called_once()
        mock_notify.assert_called_once()

    @patch("thinkisp.core.wifi.get_arp_map")
    @patch("thinkisp.core.wifi.notify")
    @patch("thinkisp.core.wifi.is_hotspot_active", return_value=False)
    @patch("thinkisp.core.wifi.run_cmd")
    def test_kick_client_by_ip(self, mock_run_cmd, mock_active, mock_notify, mock_arp):
        mock_arp.return_value = {"aa:bb:cc:dd:ee:ff": "10.42.0.77"}
        success, msg = kick_client("10.42.0.77")
        self.assertTrue(success)
        mock_run_cmd.assert_called_once_with([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "+802-11-wireless.mac-address-denylist", "aa:bb:cc:dd:ee:ff"
        ])

    @patch("thinkisp.core.wifi.get_arp_map", return_value={})
    def test_kick_client_not_found(self, mock_arp):
        success, msg = kick_client("10.42.0.99")
        self.assertFalse(success)
        self.assertIn("No se encontró", msg)

    @patch("thinkisp.core.wifi.apply_zero_latency_optimizations")
    @patch("thinkisp.core.wifi.is_hotspot_active", return_value=True)
    @patch("thinkisp.core.wifi.run_cmd")
    def test_unkick_client(self, mock_run_cmd, mock_active, mock_opt):
        success, msg = unkick_client("00:11:22:33:44:55")
        self.assertTrue(success)
        self.assertIn("desbloqueado con éxito", msg)
        mock_run_cmd.assert_any_call([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "-802-11-wireless.mac-address-denylist", "00:11:22:33:44:55"
        ])

    @patch("thinkisp.core.wifi.run_cmd")
    def test_get_blocked_clients(self, mock_run_cmd):
        mock_run_cmd.return_value = "00:11:22:33:44:55, aa:bb:cc:dd:ee:ff"
        blocked = get_blocked_clients()
        self.assertEqual(blocked, ["00:11:22:33:44:55", "aa:bb:cc:dd:ee:ff"])

        mock_run_cmd.return_value = ""
        self.assertEqual(get_blocked_clients(), [])

    @patch("thinkisp.core.wifi.run_cmd")
    def test_get_stealth_mode(self, mock_run_cmd):
        mock_run_cmd.return_value = "yes"
        self.assertTrue(get_stealth_mode())
        mock_run_cmd.return_value = "sí"
        self.assertTrue(get_stealth_mode())
        mock_run_cmd.return_value = "no"
        self.assertFalse(get_stealth_mode())

    @patch("thinkisp.core.wifi.apply_zero_latency_optimizations")
    @patch("thinkisp.core.wifi.is_hotspot_active", return_value=True)
    @patch("thinkisp.core.wifi.run_cmd")
    def test_set_stealth_mode(self, mock_run_cmd, mock_active, mock_opt):
        self.assertTrue(set_stealth_mode(True))
        mock_run_cmd.assert_any_call(["nmcli", "connection", "modify", HOTSPOT_CONN, "802-11-wireless.hidden", "yes"])

        self.assertTrue(set_stealth_mode(False))
        mock_run_cmd.assert_any_call(["nmcli", "connection", "modify", HOTSPOT_CONN, "802-11-wireless.hidden", "no"])

    @patch("thinkisp.core.wifi.apply_zero_latency_optimizations")
    @patch("thinkisp.core.wifi.is_hotspot_active", return_value=True)
    @patch("thinkisp.core.wifi.run_cmd")
    def test_set_band(self, mock_run_cmd, mock_active, mock_opt):
        # 5 GHz
        success, msg = set_band("5g")
        self.assertTrue(success)
        mock_run_cmd.assert_any_call([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "802-11-wireless.band", "a",
            "802-11-wireless.channel", "149"
        ])

        # 2.4 GHz
        success, msg = set_band("2.4g")
        self.assertTrue(success)
        mock_run_cmd.assert_any_call([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "802-11-wireless.band", "bg",
            "802-11-wireless.channel", "6"
        ])

        # Auto
        success, msg = set_band("auto")
        self.assertTrue(success)
        mock_run_cmd.assert_any_call([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "802-11-wireless.band", "",
            "802-11-wireless.channel", "0"
        ])

        # Invalid band
        success, msg = set_band("bad_band")
        self.assertFalse(success)
        self.assertIn("no válida", msg)

    def test_format_signal_bar(self):
        self.assertIn("Excelente", format_signal_bar(-45))
        self.assertIn("Muy Buena", format_signal_bar(-60))
        self.assertIn("Aceptable", format_signal_bar(-70))
        self.assertIn("Débil", format_signal_bar(-85))
        self.assertEqual(format_signal_bar(None), "N/D")


if __name__ == "__main__":
    unittest.main()
