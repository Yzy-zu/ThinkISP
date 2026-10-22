"""
Unit tests for thinkisp.ui.cli module and CLI command routing.
"""

import argparse
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, call, patch

# Ensure thinkisp package is importable regardless of CWD
THINKISP_ROOT = Path(__file__).resolve().parent.parent
if str(THINKISP_ROOT) not in sys.path:
    sys.path.insert(0, str(THINKISP_ROOT))

from main import build_parser, main
from thinkisp.config import DEFAULT_PSK, DEFAULT_SSID, ETH_IFACE, HOTSPOT_CONN
from thinkisp.ui.cli import (
    cmd_adblock,
    cmd_band,
    cmd_blocked,
    cmd_boost,
    cmd_clients,
    cmd_dns,
    cmd_gaming,
    cmd_kick,
    cmd_limit,
    cmd_off,
    cmd_on,
    cmd_qr,
    cmd_speed,
    cmd_stats,
    cmd_status,
    cmd_stealth,
    cmd_timer,
    cmd_toggle,
    cmd_unkick,
    cmd_widget,
)


class TestCliCommands(unittest.TestCase):
    """Tests for CLI handler functions in thinkisp.ui.cli."""

    @patch("thinkisp.ui.cli.start_daemon")
    @patch("thinkisp.ui.cli.start_hotspot", return_value=(True, "OK"))
    @patch("thinkisp.ui.cli.get_ethernet_status", return_value={"interface": ETH_IFACE, "connected": True, "speed": "1000Mb/s"})
    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=False)
    @patch("thinkisp.ui.cli.get_hotspot_ip", return_value="10.42.0.1/24")
    @patch("thinkisp.ui.cli.get_ip_forward_status", return_value=True)
    @patch("thinkisp.ui.cli.get_stealth_mode", return_value=False)
    @patch("thinkisp.ui.cli.load_state", return_value={})
    def test_cmd_on(self, mock_load, mock_stealth, mock_fwd, mock_ip, mock_active, mock_eth, mock_start, mock_daemon):
        args = argparse.Namespace(command="on")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_on(args)
            output = mock_out.getvalue()

        self.assertIn("INICIANDO ZONA WI-FI", output)
        self.assertIn(DEFAULT_SSID, output)
        self.assertIn("10.42.0.1/24", output)
        mock_start.assert_called_once()
        mock_daemon.assert_called_once()

    @patch("thinkisp.ui.cli.cmd_status")
    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=True)
    def test_cmd_on_already_active(self, mock_active, mock_status):
        args = argparse.Namespace(command="on")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_on(args)
            output = mock_out.getvalue()

        self.assertIn("ya está activo", output)
        mock_status.assert_called_once_with(args)

    @patch("thinkisp.ui.cli.stop_hotspot")
    @patch("thinkisp.ui.cli.cancel_timer")
    @patch("thinkisp.ui.cli.stop_daemon")
    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=True)
    def test_cmd_off_active(self, mock_active, mock_daemon, mock_timer, mock_stop):
        summary = {"duration": 120, "downloaded": 1048576, "uploaded": 524288}
        mock_stop.return_value = (True, summary)

        args = argparse.Namespace(command="off")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_off(args)
            output = mock_out.getvalue()

        self.assertIn("apagada con éxito", output)
        self.assertIn("RESUMEN DE LA SESIÓN", output)
        self.assertIn("1.0 MB", output)
        mock_daemon.assert_called_once()
        mock_timer.assert_called_once()
        mock_stop.assert_called_once()

    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=False)
    def test_cmd_off_inactive(self, mock_active):
        args = argparse.Namespace(command="off")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_off(args)
            output = mock_out.getvalue()

        self.assertIn("no está activa", output)

    @patch("thinkisp.ui.cli.apply_boost_mode")
    @patch("thinkisp.ui.cli.get_ethernet_status", return_value={"interface": ETH_IFACE, "connected": True, "speed": "1000Mb/s"})
    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=True)
    def test_cmd_boost(self, mock_active, mock_eth, mock_boost):
        args = argparse.Namespace(command="boost")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_boost(args)
            output = mock_out.getvalue()

        self.assertIn("THINKISP BOOST", output)
        self.assertIn("CAKE", output)
        mock_boost.assert_called_once()

    @patch("thinkisp.ui.cli.get_connected_clients")
    @patch("thinkisp.ui.cli.run_cmd", return_value="root cake")
    @patch("thinkisp.ui.cli.get_stealth_mode", return_value=False)
    @patch("thinkisp.ui.cli.load_state", return_value={"gaming_mode": True, "dns": "Cloudflare DNS"})
    @patch("thinkisp.ui.cli.get_ip_forward_status", return_value=True)
    @patch("thinkisp.ui.cli.get_ethernet_status", return_value={"connected": True, "speed": "1000Mb/s"})
    @patch("thinkisp.ui.cli.get_hotspot_ip", return_value="10.42.0.1/24")
    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=True)
    def test_cmd_status_active(self, mock_act, mock_ip, mock_eth, mock_fwd, mock_load, mock_st, mock_q, mock_clients):
        mock_clients.return_value = [
            {"ip": "10.42.0.50", "mac": "00:11:22:33:44:55", "signal": -45, "tx_mbps": 866.7}
        ]
        args = argparse.Namespace(command="status")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_status(args)
            output = mock_out.getvalue()

        self.assertIn("ACTIVO (Transmitiendo)", output)
        self.assertIn("10.42.0.50", output)
        self.assertIn("00:11:22:33:44:55", output)
        self.assertIn("866.7 Mbps", output)

    @patch("thinkisp.ui.cli.get_connected_clients")
    def test_cmd_clients(self, mock_clients):
        mock_clients.return_value = [
            {"ip": "10.42.0.12", "mac": "aa:bb:cc:dd:ee:ff", "signal": -55, "tx_mbps": 433.3}
        ]
        args = argparse.Namespace(command="clients")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_clients(args)
            output = mock_out.getvalue()

        self.assertIn("Dispositivos Conectados (1)", output)
        self.assertIn("10.42.0.12", output)
        self.assertIn("aa:bb:cc:dd:ee:ff", output)

    @patch("thinkisp.ui.cli.kick_client", return_value=(True, "Expulsado"))
    def test_cmd_kick(self, mock_kick):
        args = argparse.Namespace(command="kick", target="00:11:22:33:44:55")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_kick(args)
            output = mock_out.getvalue()

        self.assertIn("Expulsado", output)
        mock_kick.assert_called_once_with("00:11:22:33:44:55")

    @patch("thinkisp.ui.cli.unkick_client", return_value=(True, "Desbloqueado"))
    def test_cmd_unkick(self, mock_unkick):
        args = argparse.Namespace(command="unkick", target="00:11:22:33:44:55")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_unkick(args)
            output = mock_out.getvalue()

        self.assertIn("Desbloqueado", output)
        mock_unkick.assert_called_once_with("00:11:22:33:44:55")

    @patch("thinkisp.ui.cli.get_blocked_clients", return_value=["00:11:22:33:44:55"])
    def test_cmd_blocked(self, mock_blocked):
        args = argparse.Namespace(command="blocked")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_blocked(args)
            output = mock_out.getvalue()

        self.assertIn("00:11:22:33:44:55", output)

    @patch("thinkisp.ui.cli.set_stealth_mode")
    @patch("thinkisp.ui.cli.get_stealth_mode", return_value=True)
    def test_cmd_stealth(self, mock_get_st, mock_set_st):
        # Query status
        args_stat = argparse.Namespace(command="stealth", mode="status")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_stealth(args_stat)
            self.assertIn("ACTIVADO (Red invisible)", mock_out.getvalue())

        # Enable
        args_on = argparse.Namespace(command="stealth", mode="on")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_stealth(args_on)
            self.assertIn("Modo Stealth ACTIVADO", mock_out.getvalue())
        mock_set_st.assert_called_with(True)

    @patch("thinkisp.ui.cli.set_band", return_value=(True, "Banda cambiada a 5g."))
    def test_cmd_band(self, mock_band):
        args = argparse.Namespace(command="band", band="5g")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_band(args)
            self.assertIn("Banda cambiada a 5g", mock_out.getvalue())
        mock_band.assert_called_once_with("5g")

    @patch("thinkisp.ui.cli.set_gaming_mode", return_value=(True, "Modo Gaming activado"))
    def test_cmd_gaming(self, mock_gaming):
        args = argparse.Namespace(command="gaming", mode="on")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_gaming(args)
            self.assertIn("Modo Gaming activado", mock_out.getvalue())
        mock_gaming.assert_called_once_with(True)

    @patch("thinkisp.ui.cli.set_rate_limit")
    def test_cmd_limit(self, mock_limit):
        args = argparse.Namespace(command="limit", speed_or_action="30m")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_limit(args)
            self.assertIn("30.0 Mbps", mock_out.getvalue())
        mock_limit.assert_called_once_with(30.0)

    @patch("thinkisp.ui.cli.set_adblock", return_value=(True, "AdBlock activado"))
    def test_cmd_adblock(self, mock_adblock):
        args = argparse.Namespace(command="adblock", mode="on")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_adblock(args)
            self.assertIn("AdBlock activado", mock_out.getvalue())
        mock_adblock.assert_called_once_with(True)

    @patch("thinkisp.ui.cli.set_dns_provider", return_value=(True, "DNS cambiado"))
    def test_cmd_dns(self, mock_dns):
        args = argparse.Namespace(command="dns", provider="cloudflare")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_dns(args)
            self.assertIn("DNS cambiado", mock_out.getvalue())
        mock_dns.assert_called_once_with("cloudflare")

    @patch("thinkisp.ui.cli.set_timer_countdown", return_value=(True, "Temporizador programado"))
    def test_cmd_timer(self, mock_timer):
        args = argparse.Namespace(command="timer", action_or_duration="45m", extra_arg=None)
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_timer(args)
            self.assertIn("Temporizador programado", mock_out.getvalue())
        mock_timer.assert_called_once_with(2700)

    @patch("thinkisp.ui.cli.run_speedtest")
    @patch("thinkisp.ui.cli.get_ethernet_status", return_value={"interface": ETH_IFACE, "connected": True, "speed": "1000Mb/s"})
    def test_cmd_speed(self, mock_eth, mock_test):
        mock_test.return_value = {
            "ping_ms": 12.4,
            "down_mbps": 850.5,
            "down_mbs": 101.3,
            "up_mbps": 420.2,
            "up_mbs": 50.1,
            "error": None
        }
        args = argparse.Namespace(command="speed")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_speed(args)
            output = mock_out.getvalue()

        self.assertIn("850.5 Mbps", output)
        self.assertIn("12.4 ms", output)

    @patch("thinkisp.ui.cli.load_state")
    @patch("thinkisp.ui.cli.get_iface_bytes", return_value=(1000000, 2000000))
    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=True)
    def test_cmd_stats(self, mock_active, mock_bytes, mock_load):
        mock_load.return_value = {
            "session": {"start_time": 1000.0, "start_rx": 500000, "start_tx": 1000000}
        }
        args = argparse.Namespace(command="stats")
        with patch("time.time", return_value=1100.0):
            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                cmd_stats(args)
                output = mock_out.getvalue()

        self.assertIn("SESIÓN EN CURSO", output)
        self.assertIn("1m 40s", output)

    @patch("thinkisp.ui.cli.get_connected_clients", return_value=[{"ip": "10.42.0.2"}])
    @patch("thinkisp.ui.cli.get_hotspot_ip", return_value="10.42.0.1/24")
    @patch("thinkisp.ui.cli.get_ethernet_status", return_value={"connected": True, "speed": "1000Mb/s"})
    @patch("thinkisp.ui.cli.load_state", return_value={})
    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=True)
    def test_cmd_widget(self, mock_active, mock_load, mock_eth, mock_ip, mock_clients):
        args = argparse.Namespace(command="widget", watch=False)
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_widget(args)
            output = mock_out.getvalue().strip()

        data = json.loads(output)
        self.assertEqual(data["alt"], "active")
        self.assertEqual(data["clients"], 1)
        self.assertEqual(data["percentage"], 100)

    @patch("shutil.which", return_value=None)
    def test_cmd_qr(self, mock_which):
        args = argparse.Namespace(command="qr")
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            cmd_qr(args)
            output = mock_out.getvalue()

        self.assertIn(DEFAULT_SSID, output)
        self.assertIn(DEFAULT_PSK, output)

    @patch("thinkisp.ui.cli.cmd_off")
    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=True)
    def test_cmd_toggle_turns_off(self, mock_active, mock_off):
        args = argparse.Namespace(command="toggle")
        cmd_toggle(args)
        mock_off.assert_called_once_with(args)

    @patch("thinkisp.ui.cli.cmd_on")
    @patch("thinkisp.ui.cli.is_hotspot_active", return_value=False)
    def test_cmd_toggle_turns_on(self, mock_active, mock_on):
        args = argparse.Namespace(command="toggle")
        cmd_toggle(args)
        mock_on.assert_called_once_with(args)


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
