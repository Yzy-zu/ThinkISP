"""Panel inicial: editar, cancelar, validar y guardar sin tocar la red real."""
from contextlib import ExitStack
import io
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from thinkisp.core.network import CommandError, ThinkISPError
from thinkisp.core.profile import Capabilities, Channel
from thinkisp.ui import setup, tui


class TestSetupPanel(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.caps = Capabilities('wifi-test', 'phy7', 'driver-test',
                                 [Channel(6, 2437, 20)], widths=['auto', '20', '40'])
        stack.enter_context(patch.object(setup, 'inspect_capabilities', return_value=self.caps))
        stack.enter_context(patch.object(setup, 'profile_uuid', return_value=None))
        stack.enter_context(patch.object(setup.sys.stdin, 'isatty', return_value=True))
        stack.enter_context(patch.object(setup.getpass, 'getpass', return_value='UnaClaveSoloDePrueba!'))
        stack.enter_context(patch.object(setup, 'is_hotspot_active', return_value=False))
        self.save = stack.enter_context(patch.object(setup, 'save_profile', return_value='new-uuid'))
        self.output = stack.enter_context(patch('sys.stdout', new_callable=io.StringIO))
        self.errors = stack.enter_context(patch('sys.stderr', new_callable=io.StringIO))

    def test_cancel_first_setup_does_not_create_a_profile(self):
        with patch('builtins.input', side_effect=['Mi red', 'c']):
            with self.assertRaises(KeyboardInterrupt):
                setup.configure_hotspot()
        self.save.assert_not_called()

    def test_initial_save_uses_user_credentials_not_a_shared_password(self):
        with patch('builtins.input', side_effect=['Mi red local', 's']):
            self.assertEqual(setup.configure_hotspot(), 'new-uuid')
        values = self.save.call_args.args[0]
        self.assertEqual(values['802-11-wireless.ssid'], 'Mi red local')
        self.assertEqual(values['802-11-wireless-security.psk'], 'UnaClaveSoloDePrueba!')
        self.assertNotIn('UnaClaveSoloDePrueba!', self.output.getvalue())
        self.assertIn('driver-test', self.output.getvalue())

    def test_panel_edits_band_channel_power_hidden_and_width(self):
        choices = ['Mi red', '4', '2', '5', '2', '7', '17', '8', '2', '6', '2', 's']
        with patch('builtins.input', side_effect=choices):
            setup.configure_hotspot()
        values, caps, power = self.save.call_args.args
        self.assertEqual(values['802-11-wireless.band'], 'bg')
        self.assertEqual(values['802-11-wireless.channel'], '6')
        self.assertEqual(values['802-11-wireless.channel-width'], '20')
        self.assertEqual(values['802-11-wireless.hidden'], 'yes')
        self.assertEqual(power, 17)

    def test_out_of_range_power_is_rejected_before_save(self):
        with patch('builtins.input', side_effect=['Mi red', '7', '99', 's', 'c']):
            with self.assertRaises(KeyboardInterrupt):
                setup.configure_hotspot()
        self.save.assert_not_called()
        self.assertIn('potencia', self.errors.getvalue())

    def test_unsupported_option_is_explicit(self):
        with patch('builtins.input', side_effect=['Mi red', '11', 'c']):
            with self.assertRaises(KeyboardInterrupt):
                setup.configure_hotspot()
        self.assertIn('no expone', self.errors.getvalue())
        self.save.assert_not_called()

    def test_save_failure_does_not_print_success(self):
        self.save.side_effect = CommandError(['nmcli'], 'Permission denied')
        with patch('builtins.input', side_effect=['Mi red', 's', 'c']):
            with self.assertRaises(KeyboardInterrupt):
                setup.configure_hotspot()
        self.assertNotIn('Configuración guardada', self.output.getvalue())
        self.assertIn('Permission denied', self.errors.getvalue())

    def test_noninteractive_first_run_requires_explicit_setup(self):
        with patch.object(setup.sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(ThinkISPError, 'terminal interactiva'):
                setup.configure_hotspot()
        self.save.assert_not_called()

    def test_existing_profile_skips_first_run_wizard(self):
        with patch.object(setup, 'profile_uuid', return_value='existing'), patch.object(setup, 'configure_hotspot') as configure:
            setup.ensure_setup()
        configure.assert_not_called()

    def test_missing_profile_opens_first_run_wizard(self):
        with patch.object(setup, 'configure_hotspot') as configure:
            setup.ensure_setup()
        configure.assert_called_once()


class TestDashboard(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(tui.curses, 'has_colors', return_value=False))
        stack.enter_context(patch.object(tui.curses, 'color_pair', return_value=0))
        self.state = tui.TUIState()

    def test_on_failure_is_shown_as_error_and_watcher_is_not_started(self):
        self.state.hotspot_active = False
        with patch.object(tui, 'ensure_setup'), patch.object(tui, 'start_hotspot', side_effect=CommandError(['nmcli'], 'Not authorized')), \
             patch.object(tui, 'start_daemon') as daemon:
            tui.execute_button_action('toggle', self.state)
        self.assertIn('Error:', self.state.status_msg)
        self.assertIn('Not authorized', self.state.status_msg)
        daemon.assert_not_called()

    def test_false_backend_result_is_not_a_success_banner(self):
        with patch.object(tui, 'set_gaming_mode', return_value=(False, 'Rejected')):
            tui.execute_button_action('gaming', self.state)
        self.assertEqual(self.state.status_msg, 'Error: Rejected')

    def test_header_uses_actual_machine_and_interface(self):
        self.state.hostname = 'workstation-prueba'
        self.state.interface = 'usb-wifi'
        self.state.band = '2462 MHz · canal 11 · 20 MHz'
        with patch.object(tui, 'safe_addstr') as write:
            tui.render_header(MagicMock(), self.state, 30, 120)
        text = '\n'.join(call.args[3] for call in write.call_args_list)
        self.assertIn('workstation-prueba', text)
        self.assertIn('usb-wifi', text)
        self.assertIn('2462 MHz', text)
        self.assertNotIn('ThinkPad', text)

    def test_link_speed_is_not_labeled_gigabit(self):
        self.state.ethernet = {'connected': True, 'interface': 'usb-net', 'speed': '100Mb/s'}
        with patch.object(tui, 'draw_box'), patch.object(tui, 'safe_addstr') as write:
            tui.render_status_bar(MagicMock(), self.state, 2, 140)
        text = '\n'.join(call.args[3] for call in write.call_args_list)
        self.assertIn('100Mb/s', text)
        self.assertNotIn('Gigabit', text)

    def test_configuration_restores_curses_after_error(self):
        with patch.object(tui.curses, 'def_prog_mode'), patch.object(tui.curses, 'endwin'), \
             patch.object(tui.curses, 'reset_prog_mode') as reset, patch.object(tui.curses, 'doupdate'), \
             patch.object(tui, 'configure_hotspot', side_effect=ThinkISPError('Rejected')):
            tui.execute_button_action('config', self.state)
        reset.assert_called_once()
        self.assertEqual(self.state.status_msg, 'Error: Rejected')

    def test_no_arguments_requires_setup_before_dashboard(self):
        with patch.object(tui, 'ensure_setup', side_effect=ThinkISPError('setup required')), patch.object(tui.curses, 'wrapper') as dashboard:
            with self.assertRaises(ThinkISPError):
                tui.launch_tui()
        dashboard.assert_not_called()


if __name__ == '__main__':
    unittest.main()
