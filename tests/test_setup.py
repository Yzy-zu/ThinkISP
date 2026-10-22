"""Panel inicial: editar, cancelar, validar y guardar sin tocar la red real."""
from contextlib import ExitStack
import io
import curses
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from thinkisp.core.network import CommandError, ThinkISPError
from thinkisp.core.profile import Capabilities, Channel
from thinkisp.ui import setup, tui


class TestSetupPanel(unittest.TestCase):
    def setUp(self):
        self.caps = Capabilities('wifi-test', 'phy7', 'driver-test',
                                 [Channel(6, 2437, 20)], widths=['auto', '20', '40'],
                                 optional={'802-11-wireless.channel-width': True})
        self.ui = MagicMock()
        self.values = dict(setup.BASE_FIELDS)
        self.values['802-11-wireless.ssid'] = 'Mi red'
        self.values['802-11-wireless-security.psk'] = 'UnaClaveSoloDePrueba!'
        self.form = setup.SetupForm(self.ui, (self.caps, None, self.values, None))

    def test_first_run_uses_machine_ssid_and_no_default_password(self):
        with patch.object(setup, 'inspect_capabilities', return_value=self.caps), \
             patch.object(setup, 'profile_uuid', return_value=None), \
             patch.object(setup, 'default_ssid', return_value='equipo-prueba'):
            _, existing, settings, _ = setup.load_configuration()
        self.assertIsNone(existing)
        self.assertEqual(settings['802-11-wireless.ssid'], 'equipo-prueba')
        self.assertEqual(settings['802-11-wireless-security.psk'], '')

    def test_password_display_never_contains_secret(self):
        self.assertNotIn(self.values['802-11-wireless-security.psk'], self.form.display('802-11-wireless-security.psk'))

    def test_password_mismatch_keeps_original(self):
        self.ui.edit.side_effect = ['OtraClaveSegura', 'NoCoincide']
        with self.assertRaisesRegex(ThinkISPError, 'no coinciden'):
            self.form.edit_field('Contraseña', '802-11-wireless-security.psk', '')
        self.assertEqual(self.values['802-11-wireless-security.psk'], 'UnaClaveSoloDePrueba!')
        self.assertFalse(self.form.dirty)

    def test_cancel_edit_does_not_mark_form_dirty(self):
        self.ui.edit.return_value = None
        self.form.edit_field('Nombre', '802-11-wireless.ssid', '')
        self.assertFalse(self.form.dirty)

    def test_band_change_clears_channel_and_width(self):
        self.values.update({'802-11-wireless.channel': '44', '802-11-wireless.channel-width': '80'})
        self.ui.choose.return_value = 'bg'
        self.form.edit_field('Banda', '802-11-wireless.band', '')
        self.assertEqual(self.values['802-11-wireless.channel'], '0')
        self.assertNotIn('802-11-wireless.channel-width', self.values)
        self.assertTrue(self.form.dirty)

    def test_unavailable_radio_bands_are_not_in_selector(self):
        self.assertEqual(self.form.choices('802-11-wireless.band'), [('', 'Automática'), ('bg', '2.4 GHz')])

    def test_channel_requires_band(self):
        with self.assertRaisesRegex(ThinkISPError, 'primero una banda'):
            self.form.edit_field('Canal', '802-11-wireless.channel', '')
        self.ui.choose.assert_not_called()

    def test_unsupported_options_cannot_be_edited(self):
        with self.assertRaisesRegex(ThinkISPError, 'no expone'):
            self.form.edit_field('Aislar', '802-11-wireless.ap-isolation', '')
        self.ui.edit.assert_not_called()

    def test_out_of_range_power_is_rejected_before_save(self):
        with patch.object(setup, 'save_profile') as save:
            with self.assertRaisesRegex(ThinkISPError, 'potencia'):
                setup.persist_configuration(self.values, self.caps, 99)
        save.assert_not_called()

    def test_save_inactive_does_not_activate_ap(self):
        with patch.object(setup, 'is_hotspot_active', return_value=False), \
             patch.object(setup, 'save_profile', return_value='new-uuid') as save, \
             patch.object(setup, 'run_cmd') as run:
            self.assertEqual(setup.persist_configuration(self.values, self.caps, None), 'new-uuid')
        save.assert_called_once()
        run.assert_not_called()

    def test_save_failure_is_propagated(self):
        with patch.object(setup, 'is_hotspot_active', return_value=False), \
             patch.object(setup, 'save_profile', side_effect=ThinkISPError('Rejected')):
            with self.assertRaisesRegex(ThinkISPError, 'Rejected'):
                setup.persist_configuration(self.values, self.caps, None)

    def test_escape_discards_changes_without_saving(self):
        self.form.settings['802-11-wireless.ssid'] = 'Cambio sin guardar'
        self.ui.screen.getmaxyx.return_value = (24, 80)
        self.ui.frame.return_value = (24, 80)
        self.ui.key.return_value = '\x1b'
        self.ui.choose.return_value = 'discard'
        with patch('thinkisp.ui.widgets.put'), patch('thinkisp.ui.widgets.accent', return_value=0), \
             patch.object(setup, 'persist_configuration') as save:
            with self.assertRaises(KeyboardInterrupt):
                self.form.run()
        save.assert_not_called()

    def test_failed_save_preserves_edits_and_keeps_form_open(self):
        self.form.settings['802-11-wireless.ssid'] = 'Cambio pendiente'
        self.ui.screen.getmaxyx.return_value = (24, 80)
        self.ui.frame.return_value = (24, 80)
        self.ui.key.side_effect = [curses.KEY_F2, '\x1b']
        self.ui.choose.side_effect = ['save', 'discard']
        self.ui.work.side_effect = ThinkISPError('Permiso rechazado')
        with patch('thinkisp.ui.widgets.put'), patch('thinkisp.ui.widgets.accent', return_value=0):
            with self.assertRaises(KeyboardInterrupt):
                self.form.run()
        self.assertEqual(self.form.error, 'Permiso rechazado')
        self.assertEqual(self.form.settings['802-11-wireless.ssid'], 'Cambio pendiente')

    def test_noninteractive_first_run_requires_explicit_setup(self):
        with patch.object(setup.sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(ThinkISPError, 'terminal interactiva'):
                setup.configure_hotspot()

    def test_existing_profile_skips_first_run_wizard(self):
        with patch.object(setup, 'profile_uuid', return_value='existing'), patch.object(setup, 'configure_hotspot') as configure:
            setup.ensure_setup()
        configure.assert_not_called()

    def test_missing_profile_opens_first_run_wizard(self):
        with patch.object(setup, 'profile_uuid', return_value=None), patch.object(setup, 'configure_hotspot') as configure:
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

    def test_configuration_stays_in_curses_and_restores_dashboard_after_error(self):
        self.state.screen = MagicMock()
        with patch.object(tui, 'init_colors'), patch.object(tui.curses, 'endwin') as end, \
             patch.object(tui, 'configure_hotspot', side_effect=ThinkISPError('Rejected')) as configure:
            tui.execute_button_action('config', self.state)
        configure.assert_called_once_with(screen=self.state.screen)
        end.assert_not_called()
        self.state.screen.clear.assert_called_once()
        self.assertEqual(self.state.status_msg, 'Error: Rejected')

    def test_first_run_stays_inside_curses_before_dashboard(self):
        screen = MagicMock()
        with patch.object(tui.sys.stdin, 'isatty', return_value=True), \
             patch.object(tui.sys.stdout, 'isatty', return_value=True), \
             patch.dict('os.environ', {'TERM': 'xterm'}), \
             patch.object(tui.locale, 'nl_langinfo', return_value='UTF-8'), \
             patch.object(tui, 'ensure_setup', side_effect=ThinkISPError('setup required')) as setup_first, \
             patch.object(tui, 'run_tui') as panel, \
             patch.object(tui.curses, 'wrapper', side_effect=lambda fn: fn(screen)):
            with self.assertRaisesRegex(ThinkISPError, 'setup required'):
                tui.launch_tui()
        setup_first.assert_called_once_with(screen=screen)
        panel.assert_not_called()


if __name__ == '__main__':
    unittest.main()
