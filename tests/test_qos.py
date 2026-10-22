"""
Unit tests for thinkisp.core.qos module.
"""

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure thinkisp package is importable regardless of CWD
THINKISP_ROOT = Path(__file__).resolve().parent.parent
if str(THINKISP_ROOT) not in sys.path:
    sys.path.insert(0, str(THINKISP_ROOT))

from thinkisp.config import HOTSPOT_CONN, THINKPAD_MODE_BIN
from thinkisp.core.qos import (
    apply_boost_mode,
    apply_rate_limit,
    clear_rate_limit,
    disable_gaming_mode,
    enable_gaming_mode,
    get_current_qos,
    get_gaming_mode,
    get_rate_limit,
    set_gaming_mode,
    set_rate_limit,
    set_thinkpad_performance,
)


class TestQosGamingMode(unittest.TestCase):
    """Tests for CAKE qdisc gaming mode toggle and query."""

    @patch("thinkisp.core.qos.notify")
    @patch("thinkisp.core.qos.save_state")
    @patch("thinkisp.core.qos.load_state", return_value={})
    @patch("thinkisp.core.qos.is_hotspot_active", return_value=True)
    @patch("thinkisp.core.qos.apply_zero_latency_optimizations")
    @patch("thinkisp.core.qos.run_cmd")
    def test_enable_gaming_mode(
        self, mock_run_cmd, mock_opt, mock_active, mock_load, mock_save, mock_notify
    ):
        with patch.object(Path, "exists", return_value=False):
            success, msg = enable_gaming_mode()

        self.assertTrue(success)
        self.assertIn("activado", msg)

        # Asserts CAKE qdisc configured
        mock_run_cmd.assert_any_call([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "tc.qdiscs", "root cake",
            "802-11-wireless.powersave", "2"
        ])
        mock_run_cmd.assert_any_call(["nmcli", "connection", "up", HOTSPOT_CONN])
        mock_opt.assert_called()

        # Asserts state saved
        mock_save.assert_called_once_with({"gaming_mode": True})
        mock_notify.assert_called_once()

    @patch("thinkisp.core.qos.notify")
    @patch("thinkisp.core.qos.save_state")
    @patch("thinkisp.core.qos.load_state", return_value={"gaming_mode": True})
    @patch("thinkisp.core.qos.is_hotspot_active", return_value=True)
    @patch("thinkisp.core.qos.apply_zero_latency_optimizations")
    @patch("thinkisp.core.qos.run_cmd")
    def test_disable_gaming_mode(
        self, mock_run_cmd, mock_opt, mock_active, mock_load, mock_save, mock_notify
    ):
        success, msg = disable_gaming_mode()
        self.assertTrue(success)
        self.assertIn("desactivado", msg)

        mock_run_cmd.assert_any_call(["nmcli", "connection", "modify", HOTSPOT_CONN, "tc.qdiscs", ""])
        mock_run_cmd.assert_any_call(["nmcli", "connection", "up", HOTSPOT_CONN])
        mock_save.assert_called_once_with({"gaming_mode": False})
        mock_notify.assert_called_once()

    @patch("thinkisp.core.qos.load_state")
    def test_get_gaming_mode(self, mock_load):
        mock_load.return_value = {"gaming_mode": True}
        self.assertTrue(get_gaming_mode())

        mock_load.return_value = {}
        self.assertFalse(get_gaming_mode())


class TestQosRateLimit(unittest.TestCase):
    """Tests for TBF qdisc rate limiting and clearing."""

    @patch("thinkisp.core.qos.notify")
    @patch("thinkisp.core.qos.save_state")
    @patch("thinkisp.core.qos.load_state", return_value={})
    @patch("thinkisp.core.qos.is_hotspot_active", return_value=True)
    @patch("thinkisp.core.qos.apply_zero_latency_optimizations")
    @patch("thinkisp.core.qos.run_cmd")
    def test_apply_rate_limit(
        self, mock_run_cmd, mock_opt, mock_active, mock_load, mock_save, mock_notify
    ):
        # 25 Mbps = 25,000,000 / 8 = 3125000 bytes/sec
        success, msg = apply_rate_limit(25)
        self.assertTrue(success)
        self.assertIn("25 Mbps", msg)

        mock_run_cmd.assert_any_call([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "tc.qdiscs", "root tbf burst 32768 latency 50 rate 3125000"
        ])
        mock_run_cmd.assert_any_call(["nmcli", "connection", "up", HOTSPOT_CONN])
        mock_save.assert_called_once_with({"rate_limit": "25 Mbps"})
        mock_notify.assert_called_once()

    @patch("thinkisp.core.qos.notify")
    @patch("thinkisp.core.qos.save_state")
    @patch("thinkisp.core.qos.load_state", return_value={"rate_limit": "50 Mbps"})
    @patch("thinkisp.core.qos.is_hotspot_active", return_value=False)
    @patch("thinkisp.core.qos.run_cmd")
    def test_clear_rate_limit(
        self, mock_run_cmd, mock_active, mock_load, mock_save, mock_notify
    ):
        success, msg = clear_rate_limit()
        self.assertTrue(success)
        self.assertIn("eliminado", msg)

        mock_run_cmd.assert_called_once_with(["nmcli", "connection", "modify", HOTSPOT_CONN, "tc.qdiscs", ""])
        mock_save.assert_called_once_with({})
        mock_notify.assert_called_once()

    @patch("thinkisp.core.qos.load_state")
    def test_get_rate_limit(self, mock_load):
        mock_load.return_value = {"rate_limit": "15 Mbps"}
        self.assertEqual(get_rate_limit(), "15 Mbps")

        mock_load.return_value = {}
        self.assertIsNone(get_rate_limit())


class TestQosStatusAndThinkPad(unittest.TestCase):
    """Tests for composite QoS status and ThinkPad mode binary integration."""

    @patch("thinkisp.core.qos.load_state")
    def test_get_current_qos(self, mock_load):
        mock_load.return_value = {"gaming_mode": True, "rate_limit": "20 Mbps"}
        qos = get_current_qos()
        self.assertEqual(qos["gaming_mode"], True)
        self.assertEqual(qos["rate_limit"], "20 Mbps")

        mock_load.return_value = {}
        qos_empty = get_current_qos()
        self.assertEqual(qos_empty["gaming_mode"], False)
        self.assertIsNone(qos_empty["rate_limit"])

    @patch("thinkisp.core.qos.run_cmd")
    def test_set_thinkpad_performance_installed(self, mock_run_cmd):
        with patch.object(Path, "exists", return_value=True):
            res = set_thinkpad_performance()
            self.assertTrue(res)
            mock_run_cmd.assert_called_once_with([str(THINKPAD_MODE_BIN), "performance"])

    @patch("thinkisp.core.qos.shutil.which", return_value=None)
    @patch("thinkisp.core.qos.run_cmd")
    def test_set_thinkpad_performance_not_installed(self, mock_run_cmd, mock_which):
        with patch.object(Path, "exists", return_value=False):
            res = set_thinkpad_performance()
            self.assertFalse(res)
            mock_run_cmd.assert_not_called()

    @patch("thinkisp.core.qos.notify")
    @patch("thinkisp.core.qos.save_state")
    @patch("thinkisp.core.qos.load_state", return_value={})
    @patch("thinkisp.core.qos.apply_zero_latency_optimizations")
    @patch("thinkisp.core.qos.run_cmd")
    def test_apply_boost_mode(
        self, mock_run_cmd, mock_opt, mock_load, mock_save, mock_notify
    ):
        with patch.object(Path, "exists", return_value=False):
            success, msg = apply_boost_mode()

        self.assertTrue(success)
        self.assertIn("activado con éxito", msg)
        mock_run_cmd.assert_called_with([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "802-11-wireless.band", "a",
            "802-11-wireless.channel", "149",
            "802-11-wireless.powersave", "2",
            "tc.qdiscs", "root cake"
        ])
        mock_opt.assert_called_once()
        mock_save.assert_called_once_with({"gaming_mode": True})
        mock_notify.assert_called_once()


if __name__ == "__main__":
    unittest.main()
