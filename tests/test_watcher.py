"""
Unit tests for thinkisp.core.watcher module.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, call, patch

# Ensure thinkisp package is importable regardless of CWD
THINKISP_ROOT = Path(__file__).resolve().parent.parent
if str(THINKISP_ROOT) not in sys.path:
    sys.path.insert(0, str(THINKISP_ROOT))

from thinkisp.config import DAEMON_PID_FILE, HOTSPOT_CONN, WLAN_IFACE
from thinkisp.core.watcher import (
    HotspotWatcher,
    cancel_timer,
    get_timer_status,
    notify,
    set_timer_countdown,
    set_timer_idle,
    start_daemon,
    stop_daemon,
)


class TestHotspotWatcherEvents(unittest.TestCase):
    """Tests for HotspotWatcher event loop, client discovery, and timer actions."""

    @patch("thinkisp.core.watcher.notify")
    @patch("thinkisp.core.watcher.run_cmd")
    def test_initial_scan_registers_clients_without_events(self, mock_run_cmd, mock_notify):
        mock_run_cmd.return_value = (
            f"Station 00:11:22:33:44:55 (on {WLAN_IFACE})\n"
            f"Station aa:bb:cc:dd:ee:ff (on {WLAN_IFACE})\n"
        )
        watcher = HotspotWatcher()
        events = watcher.scan_clients()

        self.assertEqual(len(events), 0)
        self.assertEqual(watcher.known_macs, {"00:11:22:33:44:55", "aa:bb:cc:dd:ee:ff"})
        self.assertFalse(watcher.initial_scan)
        mock_notify.assert_not_called()

    @patch("thinkisp.core.watcher.get_hostname_for_ip", return_value="Pixel-8")
    @patch("thinkisp.core.watcher.get_arp_map", return_value={"11:22:33:44:55:66": "10.42.0.70"})
    @patch("thinkisp.core.watcher.notify")
    @patch("thinkisp.core.watcher.run_cmd")
    def test_client_connected_and_disconnected_events(
        self, mock_run_cmd, mock_notify, mock_arp, mock_host
    ):
        watcher = HotspotWatcher()
        watcher.initial_scan = False
        watcher.known_macs = {"00:11:22:33:44:55"}

        connected_cb = MagicMock()
        disconnected_cb = MagicMock()
        watcher.on_client_connected.append(connected_cb)
        watcher.on_client_disconnected.append(disconnected_cb)

        # 1. New client connects: 11:22:33:44:55:66
        mock_run_cmd.return_value = (
            f"Station 00:11:22:33:44:55 (on {WLAN_IFACE})\n"
            f"Station 11:22:33:44:55:66 (on {WLAN_IFACE})\n"
        )
        events = watcher.scan_clients()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "client_connected")
        self.assertEqual(events[0]["mac"], "11:22:33:44:55:66")
        self.assertEqual(events[0]["ip"], "10.42.0.70")
        connected_cb.assert_called_once()
        mock_notify.assert_called_once()

        # 2. Client disconnects: 00:11:22:33:44:55 disappears
        mock_notify.reset_mock()
        mock_run_cmd.return_value = f"Station 11:22:33:44:55:66 (on {WLAN_IFACE})\n"
        events_disconn = watcher.scan_clients()

        self.assertEqual(len(events_disconn), 1)
        self.assertEqual(events_disconn[0]["type"], "client_disconnected")
        self.assertEqual(events_disconn[0]["mac"], "00:11:22:33:44:55")
        disconnected_cb.assert_called_once()
        mock_notify.assert_called_once()

    @patch("thinkisp.core.watcher.notify")
    @patch("thinkisp.core.watcher.run_cmd")
    def test_check_timers_5min_warning(self, mock_run_cmd, mock_notify):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            tfile = Path(f.name)
            curr = time.time()
            data = {"end_time": curr + 240, "warned_5m": False}
            f.write(json.dumps(data))

        try:
            watcher = HotspotWatcher(timer_file=tfile)
            warning_cb = MagicMock()
            watcher.on_timer_warning.append(warning_cb)

            res = watcher.check_timers()
            self.assertEqual(res["action"], "warn_5m")
            warning_cb.assert_called_once()
            mock_notify.assert_called_once()

            # Verify warned_5m was set to True in the file
            updated = json.loads(tfile.read_text())
            self.assertTrue(updated["warned_5m"])

            # Subsequent check should not warn again
            res2 = watcher.check_timers()
            self.assertIsNone(res2["action"])
        finally:
            tfile.unlink(missing_ok=True)

    @patch("thinkisp.core.watcher.notify")
    @patch("thinkisp.core.watcher.run_cmd")
    def test_check_timers_countdown_expired(self, mock_run_cmd, mock_notify):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            tfile = Path(f.name)
            curr = time.time()
            data = {"end_time": curr - 5, "warned_5m": True}
            f.write(json.dumps(data))

        try:
            watcher = HotspotWatcher(timer_file=tfile)
            expired_cb = MagicMock()
            watcher.on_timer_expired.append(expired_cb)

            res = watcher.check_timers()
            self.assertEqual(res["action"], "shutdown_timer")
            expired_cb.assert_called_once()
            mock_notify.assert_called_once()
            mock_run_cmd.assert_called_with(["nmcli", "connection", "down", HOTSPOT_CONN])
            # Verify timer file was unlinked
            self.assertFalse(tfile.exists())
        finally:
            tfile.unlink(missing_ok=True)

    @patch("thinkisp.core.watcher.notify")
    @patch("thinkisp.core.watcher.run_cmd")
    def test_check_timers_idle_timeout_expiration(self, mock_run_cmd, mock_notify):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            tfile = Path(f.name)
            data = {"idle_timeout": 4}  # 4 seconds idle limit
            f.write(json.dumps(data))

        try:
            watcher = HotspotWatcher(check_interval=2.0, timer_file=tfile)
            idle_cb = MagicMock()
            watcher.on_idle_expired.append(idle_cb)
            watcher.known_macs = set()  # No connected clients

            # Tick 1: idle_seconds becomes 2
            res1 = watcher.check_timers()
            self.assertIsNone(res1["action"])
            self.assertEqual(watcher.idle_seconds, 2)

            # Tick 2: idle_seconds becomes 4 >= 4 (triggers shutdown)
            res2 = watcher.check_timers()
            self.assertEqual(res2["action"], "shutdown_idle")
            idle_cb.assert_called_once()
            mock_notify.assert_called_once()
            mock_run_cmd.assert_called_with(["nmcli", "connection", "down", HOTSPOT_CONN])
            self.assertFalse(tfile.exists())
        finally:
            tfile.unlink(missing_ok=True)

    @patch("thinkisp.core.watcher.notify")
    @patch("thinkisp.core.watcher.run_cmd")
    def test_check_timers_idle_resets_on_client_presence(self, mock_run_cmd, mock_notify):
        with tempfile.NamedTemporaryFile("w+", delete=False) as f:
            tfile = Path(f.name)
            data = {"idle_timeout": 10}
            f.write(json.dumps(data))

        try:
            watcher = HotspotWatcher(check_interval=2.0, timer_file=tfile)
            watcher.known_macs = set()
            watcher.check_timers()
            self.assertEqual(watcher.idle_seconds, 2)

            # Client connects
            watcher.known_macs = {"00:11:22:33:44:55"}
            watcher.check_timers()
            self.assertEqual(watcher.idle_seconds, 0)
        finally:
            tfile.unlink(missing_ok=True)


class TestTimerCountdownParsing(unittest.TestCase):
    """Tests for setting, querying, and cancelling countdown and idle timers."""

    def test_timer_countdown_and_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_timer_file = Path(tmpdir) / "timer.json"

            with patch("thinkisp.core.watcher.TIMER_FILE", fake_timer_file), \
                 patch("thinkisp.core.watcher.start_daemon") as mock_start_d, \
                 patch("thinkisp.core.watcher.notify") as mock_notify:

                # 1. No active timer
                self.assertIsNone(get_timer_status())

                # 2. Set countdown of 30 minutes (1800s)
                success, msg = set_timer_countdown(1800)
                self.assertTrue(success)
                self.assertIn("Temporizador programado", msg)
                self.assertTrue(fake_timer_file.exists())
                mock_start_d.assert_called_once()
                mock_notify.assert_called_once()

                # 3. Query status
                status = get_timer_status()
                self.assertIsNotNone(status)
                self.assertIn("remaining_str", status)
                self.assertIn("end_time_str", status)
                self.assertTrue(status["remaining_sec"] > 1700)

                # 4. Set idle timeout of 15 minutes
                set_timer_idle(15)
                status2 = get_timer_status()
                self.assertEqual(status2["idle_mins"], 15)

                # 5. Cancel timer
                cancel_timer()
                self.assertFalse(fake_timer_file.exists())
                self.assertIsNone(get_timer_status())


class TestDaemonProcessManagement(unittest.TestCase):
    """Tests for starting and stopping background watcher daemon."""

    @patch("subprocess.Popen")
    def test_start_daemon_spawns_process(self, mock_popen):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_pid_file = Path(tmpdir) / "daemon.pid"
            fake_state_dir = fake_pid_file.parent

            mock_proc = MagicMock(pid=99887)
            mock_popen.return_value = mock_proc

            with patch("thinkisp.core.watcher.DAEMON_PID_FILE", fake_pid_file), \
                 patch("thinkisp.core.watcher.STATE_DIR", fake_state_dir):
                start_daemon()
                mock_popen.assert_called_once()
                self.assertTrue(fake_pid_file.exists())
                self.assertEqual(fake_pid_file.read_text(), "99887")

    @patch("os.kill")
    @patch("subprocess.Popen")
    def test_start_daemon_already_running(self, mock_popen, mock_kill):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_pid_file = Path(tmpdir) / "daemon.pid"
            fake_pid_file.write_text("12345")

            # os.kill(12345, 0) succeeds -> process is alive
            mock_kill.return_value = None

            with patch("thinkisp.core.watcher.DAEMON_PID_FILE", fake_pid_file):
                start_daemon()
                mock_popen.assert_not_called()

    @patch("os.kill")
    def test_stop_daemon(self, mock_kill):
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_pid_file = Path(tmpdir) / "daemon.pid"
            fake_pid_file.write_text("12345")

            with patch("thinkisp.core.watcher.DAEMON_PID_FILE", fake_pid_file):
                stop_daemon()
                mock_kill.assert_called_once_with(12345, 15)
                self.assertFalse(fake_pid_file.exists())


if __name__ == "__main__":
    unittest.main()
