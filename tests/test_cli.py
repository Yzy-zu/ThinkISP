"""CLI: datos reales, propagación de fallos y configuración inicial."""
import argparse
from contextlib import ExitStack
import io
import json
import sys
import unittest
from unittest.mock import patch

from main import build_parser, main
from thinkisp.core.network import CommandError, ThinkISPError
from thinkisp.ui import cli


DETAILS = {"ssid": "Red del equipo de prueba", "psk": "ClavePruebaVariable!",
           "interface": "wifi-test", "radio": "2437 MHz · canal 6 · 20 MHz",
           "hidden": True, "security": "wpa-psk", "firewall": "zona-personalizada", "tx_power": "17 dBm"}


class TestCliCommands(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(cli, 'hotspot_details', return_value=dict(DETAILS)))
        self.stack.enter_context(patch.object(cli, 'ensure_setup'))
        self.output = self.stack.enter_context(patch('sys.stdout', new_callable=io.StringIO))
        self.stack.enter_context(patch.object(cli, 'WLAN_IFACE', 'wifi-test'))

    def test_on_configures_before_starting_and_uses_profile_data(self):
        events = []
        with patch.object(cli, 'ensure_setup', side_effect=lambda: events.append('setup')), \
             patch.object(cli, 'start_hotspot', side_effect=lambda: (events.append('start') or (True, 'OK'))), \
             patch.object(cli, 'start_daemon') as daemon, \
             patch.object(cli, 'get_hotspot_ip', return_value='192.168.73.1/24'):
            cli.cmd_on(argparse.Namespace())
        self.assertEqual(events, ['setup', 'start'])
        self.assertIn(DETAILS['ssid'], self.output.getvalue())
        self.assertIn(DETAILS['radio'], self.output.getvalue())
        self.assertIn('192.168.73.1/24', self.output.getvalue())
        self.assertNotIn('80 MHz', self.output.getvalue())
        daemon.assert_called_once()

    def test_on_failure_never_prints_success_or_starts_watcher(self):
        error = CommandError(['nmcli'], 'Permission denied', 4)
        with patch.object(cli, 'start_hotspot', side_effect=error), patch.object(cli, 'start_daemon') as daemon:
            with self.assertRaises(CommandError):
                cli.cmd_on(argparse.Namespace())
        self.assertNotIn('activada con éxito', self.output.getvalue())
        daemon.assert_not_called()

    def test_off_does_not_cancel_timer_before_success(self):
        with patch.object(cli, 'stop_hotspot', side_effect=CommandError(['nmcli'], 'denied')), \
             patch.object(cli, 'cancel_timer') as timer, patch.object(cli, 'stop_daemon') as daemon:
            with self.assertRaises(CommandError):
                cli.cmd_off(argparse.Namespace())
        timer.assert_not_called()
        daemon.assert_not_called()
        self.assertNotIn('apagada.', self.output.getvalue())

    def test_off_prints_recorded_summary(self):
        summary = {'duration': 120, 'downloaded': 1048576, 'uploaded': 524288}
        with patch.object(cli, 'stop_hotspot', return_value=(True, summary)), \
             patch.object(cli, 'stop_daemon'), patch.object(cli, 'cancel_timer'):
            cli.cmd_off(argparse.Namespace())
        self.assertIn('2m 00s', self.output.getvalue())
        self.assertIn('1.0 MB', self.output.getvalue())

    def test_boost_does_not_continue_after_failed_start(self):
        with patch.object(cli, 'is_hotspot_active', return_value=False), \
             patch.object(cli, 'cmd_on', side_effect=ThinkISPError('failed')), \
             patch.object(cli, 'apply_boost_mode') as boost:
            with self.assertRaises(ThinkISPError):
                cli.cmd_boost(argparse.Namespace())
        boost.assert_not_called()

    def test_status_reports_actual_radio_and_firewall(self):
        with patch.object(cli, 'is_hotspot_active', return_value=True), \
             patch.object(cli, 'get_ethernet_status', return_value={'interface': 'usb-net', 'connected': True, 'speed': '100Mb/s'}), \
             patch.object(cli, 'get_hotspot_ip', return_value='192.168.73.1/24'), \
             patch.object(cli, 'get_ip_forward_status', return_value=False), \
             patch.object(cli, 'run_cmd', return_value='qdisc fq_codel 0: root'), \
             patch.object(cli, 'get_connected_clients', return_value=[]):
            cli.cmd_status(argparse.Namespace())
        output = self.output.getvalue()
        for text in (DETAILS['ssid'], DETAILS['radio'], 'zona-personalizada', '100Mb/s', 'Inactivo'):
            self.assertIn(text, output)
        self.assertNotIn('trusted', output)
        self.assertNotIn(DETAILS['psk'], output)

    def test_widget_is_json_with_actual_ssid_and_radio(self):
        with patch.object(cli, 'is_hotspot_active', return_value=True), \
             patch.object(cli, 'get_ethernet_status', return_value={'connected': False, 'speed': 'Sin datos'}), \
             patch.object(cli, 'load_state', return_value={}), \
             patch.object(cli, 'get_connected_clients', return_value=[{}]), \
             patch.object(cli, 'get_hotspot_ip', return_value='192.168.73.1/24'):
            cli.cmd_widget(argparse.Namespace(watch=False))
        data = json.loads(self.output.getvalue())
        self.assertIn(DETAILS['ssid'], data['text'])
        self.assertIn(DETAILS['radio'], data['tooltip'])
        self.assertEqual(data['clients'], 1)

    def test_qr_uses_current_credentials(self):
        with patch.object(cli.shutil, 'which', return_value=None):
            cli.cmd_qr(argparse.Namespace())
        self.assertIn(DETAILS['ssid'], self.output.getvalue())
        self.assertIn(DETAILS['psk'], self.output.getvalue())

    def test_failed_operation_result_is_not_success(self):
        with patch.object(cli, 'set_dns_provider', return_value=(False, 'unknown provider')):
            with self.assertRaisesRegex(ThinkISPError, 'unknown provider'):
                cli.cmd_dns(argparse.Namespace(provider='missing'))

    def test_invalid_limit_is_rejected_without_mutation(self):
        for value in ['-30', '30garbage', '1.2.3']:
            with self.subTest(value=value), patch.object(cli, 'set_rate_limit') as apply:
                with self.assertRaises(ThinkISPError):
                    cli.cmd_limit(argparse.Namespace(speed_or_action=value))
                apply.assert_not_called()

    def test_entrypoint_returns_nonzero_and_preserves_error(self):
        with patch('sys.argv', ['thinkisp', 'on']), \
             patch('main.cmd_on', side_effect=CommandError(['nmcli'], 'Not authorized', 4)), \
             patch('sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(main(), 1)
        self.assertIn('Not authorized', errors.getvalue())
        self.assertNotIn('Traceback', errors.getvalue())

    def test_setup_and_config_are_routed(self):
        for command in ['setup', 'config']:
            with self.subTest(command=command), patch('sys.argv', ['thinkisp', command]), patch('main.cmd_setup') as setup:
                self.assertEqual(main(), 0)
                setup.assert_called_once()

    def test_cancelled_configuration_returns_130(self):
        with patch('sys.argv', ['thinkisp', 'setup']), patch('main.cmd_setup', side_effect=KeyboardInterrupt), patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(main(), 130)


class TestCliParserRouting(unittest.TestCase):
    """Tests for CLI arguments parsing and main dispatch routing."""

    def test_build_parser_commands(self):
        parser = build_parser()

        self.assertEqual(parser.parse_args(["on"]).command, "on")
        self.assertEqual(parser.parse_args(["off"]).command, "off")
        self.assertEqual(parser.parse_args(["boost"]).command, "boost")
        self.assertEqual(parser.parse_args(["status"]).command, "status")
        self.assertEqual(parser.parse_args(["clients"]).command, "clients")
        self.assertEqual(parser.parse_args(["speed"]).command, "speed")
        self.assertEqual(parser.parse_args(["stats"]).command, "stats")
        self.assertEqual(parser.parse_args(["qr"]).command, "qr")
        self.assertEqual(parser.parse_args(["toggle"]).command, "toggle")

        kick_args = parser.parse_args(["kick", "10.42.0.5"])
        self.assertEqual(kick_args.command, "kick")
        self.assertEqual(kick_args.target, "10.42.0.5")

        stealth_args = parser.parse_args(["stealth", "on"])
        self.assertEqual(stealth_args.command, "stealth")
        self.assertEqual(stealth_args.mode, "on")

        band_args = parser.parse_args(["band", "5g"])
        self.assertEqual(band_args.command, "band")
        self.assertEqual(band_args.band, "5g")

        gaming_args = parser.parse_args(["gaming", "on"])
        self.assertEqual(gaming_args.command, "gaming")
        self.assertEqual(gaming_args.mode, "on")

        limit_args = parser.parse_args(["limit", "50m"])
        self.assertEqual(limit_args.command, "limit")
        self.assertEqual(limit_args.speed_or_action, "50m")

        dns_args = parser.parse_args(["dns", "cloudflare"])
        self.assertEqual(dns_args.command, "dns")
        self.assertEqual(dns_args.provider, "cloudflare")

        timer_args = parser.parse_args(["timer", "1h"])
        self.assertEqual(timer_args.command, "timer")
        self.assertEqual(timer_args.action_or_duration, "1h")

    @patch("main.cmd_on")
    def test_main_dispatch_on(self, mock_cmd_on):
        with patch("sys.argv", ["thinkisp", "on"]):
            main()
        mock_cmd_on.assert_called_once()

    @patch("main.cmd_off")
    def test_main_dispatch_off(self, mock_cmd_off):
        with patch("sys.argv", ["thinkisp", "off"]):
            main()
        mock_cmd_off.assert_called_once()

    @patch("main.cmd_boost")
    def test_main_dispatch_boost(self, mock_cmd_boost):
        with patch("sys.argv", ["thinkisp", "boost"]):
            main()
        mock_cmd_boost.assert_called_once()

    @patch("main.cmd_clients")
    def test_main_dispatch_clients(self, mock_cmd_clients):
        with patch("sys.argv", ["thinkisp", "clients"]):
            main()
        mock_cmd_clients.assert_called_once()


if __name__ == "__main__":
    unittest.main()
