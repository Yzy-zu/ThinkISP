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

from thinkisp.config import HOTSPOT_CONN
WLAN_IFACE = "wifi-test"
from thinkisp.core.network import CommandError, ThinkISPError
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
    def setUp(self):
        from contextlib import ExitStack
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('thinkisp.core.wifi.WLAN_IFACE', 'wifi-test'))
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.stack.enter_context(patch('thinkisp.core.wifi.TIMER_FILE', Path(self.directory.name) / 'timer.json'))
        self.active = self.stack.enter_context(patch('thinkisp.core.wifi.is_hotspot_active', side_effect=[False, True]))
        self.run = self.stack.enter_context(patch('thinkisp.core.wifi.run_cmd', return_value='yes'))
        self.stack.enter_context(patch('thinkisp.core.wifi.require_hotspot_profile', return_value='profile-uuid'))
        self.radio = self.stack.enter_context(patch('thinkisp.core.wifi.apply_radio_settings'))
        self.stack.enter_context(patch('thinkisp.core.wifi.get_current_wifi_connection', return_value='Previous WiFi'))
        self.stack.enter_context(patch('thinkisp.core.wifi.get_iface_bytes', return_value=(15000, 28000)))
        self.load = self.stack.enter_context(patch('thinkisp.core.wifi.load_state', return_value={}))
        self.save = self.stack.enter_context(patch('thinkisp.core.wifi.save_state'))
        self.notify = self.stack.enter_context(patch('thinkisp.core.wifi.notify'))
        from contextlib import nullcontext
        self.stack.enter_context(patch('thinkisp.core.wifi.state_lock', side_effect=lambda: nullcontext(self.load.return_value)))
        self.stack.enter_context(patch('thinkisp.core.wifi.profile_uuid', return_value='profile-uuid'))
        for operation in ('restore_dns', 'apply_saved_dns', 'apply_saved_blocks', 'clear_applied_blocks'):
            self.stack.enter_context(patch('thinkisp.core.wifi.' + operation))


    def test_start_preserves_previous_wifi_and_records_session(self):
        ok, message = start_hotspot()
        self.assertTrue(ok)
        state = self.save.call_args.args[0]
        self.assertEqual(state['last_wifi'], 'Previous WiFi')
        self.assertEqual(state['session']['start_rx'], 15000)
        self.radio.assert_called_once_with('profile-uuid')
        self.assertIn('activada con éxito', message)

    def test_start_already_active_does_not_modify_profile(self):
        self.active.side_effect = None
        self.active.return_value = True
        self.assertTrue(start_hotspot()[0])
        self.run.assert_not_called()
        self.save.assert_not_called()

    def test_start_permission_error_keeps_state_and_no_success_notification(self):
        def denied(args, **kwargs):
            if 'up' in args and 'uuid' in args:
                raise CommandError(['nmcli'], 'Permission denied', 4)
            return 'yes'
        self.run.side_effect = denied
        with self.assertRaisesRegex(CommandError, 'Permission denied'):
            start_hotspot()
        self.assertNotIn('session', self.load.return_value)
        self.notify.assert_not_called()

    def test_start_rejects_adapter_without_ap(self):
        self.run.return_value = 'no'
        with self.assertRaisesRegex(ThinkISPError, 'soporte'):
            start_hotspot()
        self.assertEqual(self.run.call_count, 1)
        self.assertNotIn('session', self.load.return_value)

    def test_unconfirmed_activation_is_error(self):
        self.active.side_effect = [False, False]
        with self.assertRaisesRegex(ThinkISPError, 'confirmó'):
            start_hotspot()
        self.assertNotIn('session', self.load.return_value)
        self.notify.assert_not_called()

    def test_radio_failure_turns_ap_off_and_restores_previous_wifi(self):
        self.radio.side_effect = CommandError(['iw'], 'not permitted')
        with self.assertRaises(CommandError):
            start_hotspot()
        commands = [call.args[0] for call in self.run.call_args_list]
        self.assertIn(['nmcli', 'connection', 'down', 'uuid', 'profile-uuid'], commands)
        self.assertIn(['nmcli', 'connection', 'up', 'id', 'Previous WiFi'], commands)
        self.assertNotIn('session', self.load.return_value)

    def test_stop_failure_does_not_end_session(self):
        self.active.side_effect = None
        self.active.return_value = True
        original = {'session': {'start_time': 1000, 'start_rx': 5000, 'start_tx': 8000}}
        self.load.return_value = original
        self.run.side_effect = CommandError(['nmcli'], 'not authorized')
        with self.assertRaises(CommandError):
            stop_hotspot()
        self.assertIn('session', original)
        self.save.assert_not_called()
        self.notify.assert_not_called()

    def test_stop_success_ends_session_and_restores_wifi(self):
        self.active.side_effect = [True, False]
        self.load.return_value = {'session': {'start_time': 1000, 'start_rx': 5000, 'start_tx': 8000}, 'last_wifi': 'Previous WiFi'}
        with patch('thinkisp.core.wifi.time.time', return_value=1120):
            ok, summary = stop_hotspot()
        self.assertTrue(ok)
        self.assertEqual(summary['downloaded'], 20000)
        self.assertEqual(summary['uploaded'], 10000)
        self.assertEqual(summary['duration'], 120)
        self.assertNotIn('session', self.save.call_args.args[0])
        self.assertNotIn('last_wifi', self.save.call_args.args[0])

    def test_stop_still_active_does_not_announce_success(self):
        self.active.side_effect = [True, True]
        with self.assertRaisesRegex(ThinkISPError, 'continúa activo'):
            stop_hotspot()
        self.save.assert_not_called()
        self.notify.assert_not_called()

    def test_stop_already_inactive_returns_no_summary(self):
        self.active.side_effect = None
        self.active.return_value = False
        self.assertEqual(stop_hotspot(), (True, None))
        self.run.assert_not_called()

    def test_stop_dns_failure_still_clears_rules_timer_and_restores_wifi(self):
        self.active.side_effect = [True, False]
        self.load.return_value = {'last_wifi': 'Previous WiFi'}
        timer = Path(self.directory.name) / 'timer.json'
        timer.write_text('{}')
        with patch('thinkisp.core.wifi.restore_dns', side_effect=ThinkISPError('DNS conflict')), \
             patch('thinkisp.core.wifi.clear_applied_blocks') as clear:
            with self.assertRaisesRegex(ThinkISPError, 'DNS conflict'):
                stop_hotspot()
        clear.assert_called_once_with('profile-uuid')
        self.assertFalse(timer.exists())
        self.assertIn(call(['nmcli', 'connection', 'up', 'id', 'Previous WiFi'], check=True, timeout=65), self.run.call_args_list)
        self.notify.assert_not_called()

    def test_stop_session_save_failure_still_attempts_network_recovery(self):
        self.active.side_effect = [True, False]
        self.save.side_effect = ThinkISPError('disk full')
        with patch('thinkisp.core.wifi.restore_dns') as restore, \
             patch('thinkisp.core.wifi.clear_applied_blocks') as clear:
            with self.assertRaisesRegex(ThinkISPError, 'disk full'):
                stop_hotspot()
        restore.assert_called_once_with('profile-uuid')
        clear.assert_called_once_with('profile-uuid')

    def test_rules_are_applied_before_hotspot_activation(self):
        events = []
        def command(args, **kwargs):
            if 'up' in args:
                events.append('activate')
            return 'yes'
        self.run.side_effect = command
        with patch('thinkisp.core.wifi.apply_saved_blocks', side_effect=lambda _: events.append('filter')):
            start_hotspot()
        self.assertEqual(events[:2], ['filter', 'activate'])


class TestWifiOptimizations(unittest.TestCase):
    """Tests for low-latency tuning and QR generation."""

    def setUp(self):
        selected = patch("thinkisp.core.wifi.WLAN_IFACE", WLAN_IFACE)
        selected.start()
        self.addCleanup(selected.stop)

    @patch("thinkisp.core.wifi.run_cmd")
    def test_optimize_low_latency(self, mock_run_cmd):
        optimize_low_latency()
        mock_run_cmd.assert_any_call(["iw", "dev", WLAN_IFACE, "set", "power_save", "off"], check=True)
        self.assertEqual(mock_run_cmd.call_count, 1)

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

    def setUp(self):
        selected = patch("thinkisp.core.wifi.WLAN_IFACE", WLAN_IFACE)
        selected.start()
        self.addCleanup(selected.stop)

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

        c2 = next(c for c in clients if c["mac"] == "aa:bb:cc:dd:ee:ff")
        self.assertEqual(c2["ip"], "10.42.0.25")
        self.assertEqual(c2["hostname"], "ThinkPad-E14")
        self.assertEqual(c2["signal"], -72)


class TestClientAccessControl(unittest.TestCase):
    def test_access_control_uses_the_firewall_backend(self):
        with patch('thinkisp.core.wifi.set_client_block', return_value=(True, 'saved')) as backend:
            self.assertTrue(kick_client('02:00:00:00:00:01')[0])
            backend.assert_called_with('02:00:00:00:00:01', True)
            self.assertTrue(unkick_client('02:00:00:00:00:01')[0])
            backend.assert_called_with('02:00:00:00:00:01', False)

    @patch('thinkisp.core.wifi.run_cmd', return_value='yes')
    def test_stealth_reads_profile_setting(self, run):
        self.assertTrue(get_stealth_mode())
        run.return_value = 'no'
        self.assertFalse(get_stealth_mode())

    @patch('thinkisp.core.wifi.is_hotspot_active', return_value=False)
    @patch('thinkisp.core.wifi.run_cmd')
    def test_stealth_failure_propagates(self, run, active):
        run.side_effect = CommandError(['nmcli'], 'denied')
        with self.assertRaises(CommandError):
            set_stealth_mode(True)

    def test_band_uses_supported_channels_without_forcing_channel(self):
        from thinkisp.core.profile import Capabilities, Channel
        caps = Capabilities('wifi-test', 'phy9', 'driver-test', [Channel(1, 2412, 18)])
        with patch('thinkisp.core.wifi.inspect_capabilities', return_value=caps), \
             patch('thinkisp.core.wifi.is_hotspot_active', return_value=False), \
             patch('thinkisp.core.wifi.run_cmd') as run:
            with self.assertRaises(ThinkISPError):
                set_band('5g')
            run.assert_not_called()
            self.assertTrue(set_band('2.4g')[0])
            self.assertEqual(run.call_args.args[0][-4:], ['802-11-wireless.band', 'bg', '802-11-wireless.channel', '0'])
            self.assertTrue(run.call_args.kwargs['check'])

    def test_qr_payload_escapes_credentials_and_records_hidden_network(self):
        from thinkisp.core.wifi import wifi_qr_payload
        payload = wifi_qr_payload('Red;local', 'p:a,s;word', True)
        self.assertIn(r'S:Red\;local;', payload)
        self.assertIn(r'P:p\:a\,s\;word;', payload)
        self.assertIn('H:true;', payload)

    def test_format_signal_bar(self):
        self.assertIn('Excelente', format_signal_bar(-45))
        self.assertIn('Débil', format_signal_bar(-85))
        self.assertEqual(format_signal_bar(None), 'N/D')


if __name__ == '__main__':
    unittest.main()
