"""Regresiones de interacción, geometría y tareas de fondo del panel."""

from contextlib import ExitStack
import threading
import time
import unittest
from unittest.mock import patch

from thinkisp.ui import tui
from thinkisp.ui.runtime import PanelTasks


class Canvas:
    def __init__(self, rows=30, cols=120):
        self.rows, self.cols = rows, cols
        self.cells = [[' '] * cols for _ in range(rows)]
        self.attrs = [[0] * cols for _ in range(rows)]
        self.writes = []

    def getmaxyx(self):
        return self.rows, self.cols

    def addstr(self, y, x, text, attr=0):
        assert 0 <= y < self.rows and 0 <= x < self.cols
        assert x + tui.cell_width(text) <= self.cols
        self.writes.append((y, x, text, attr))
        for char in text:
            width = tui.cell_width(char)
            if width:
                self.cells[y][x] = char
                self.attrs[y][x] = attr
                for extra in range(1, width):
                    self.cells[y][x + extra] = ''
                    self.attrs[y][x + extra] = attr
                x += width

    def text(self):
        return '\n'.join(''.join(row) for row in self.cells)


def render_panel(canvas, state):
    rows, cols = canvas.getmaxyx()
    y = tui.render_header(canvas, state, rows, cols)
    y = tui.render_status_bar(canvas, state, y, cols)
    y = tui.render_metrics_bar(canvas, state, y, cols)
    y = tui.render_buttons(canvas, state, y, cols)
    tui.render_clients_table(canvas, state, y, rows, cols)
    tui.render_footer(canvas, state, rows, cols)
    return y


class TestPanel(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(tui.curses, 'has_colors', return_value=False))
        self.stack.enter_context(patch.object(tui.curses, 'color_pair', side_effect=lambda i: i << 8))
        self.state = tui.TUIState()

    def tasks(self):
        tasks = PanelTasks(lambda: {})
        self.state.tasks = tasks
        self.addCleanup(tasks.close)
        return tasks

    def test_screen_renders_all_sections_and_controls_at_supported_sizes(self):
        for rows, cols in [(24, 76), (24, 80), (30, 100), (40, 140)]:
            with self.subTest(size=(rows, cols)):
                canvas = Canvas(rows, cols)
                self.state.mouse_regions = []
                table_y = render_panel(canvas, self.state)
                text = canvas.text()
                for section in ['thinkISP', 'CONEXIÓN', 'ACCIONES', 'DISPOSITIVOS', 'DESCARGA']:
                    self.assertIn(section, text)
                self.assertLessEqual(table_y + 5, rows - 2)
                self.assertEqual(len([r for r in self.state.mouse_regions if r['type'] == 'button']), len(tui.BUTTON_DEFS))
                self.assertNotIn('ThinkPad', text)

    def test_unconfirmed_state_is_distinct_from_off_and_prevents_toggle(self):
        canvas = Canvas()
        render_panel(canvas, self.state)
        self.assertIn('Estado sin confirmar', canvas.text())
        with patch.object(tui, 'start_hotspot') as start:
            tui.execute_button_action('toggle', self.state)
        start.assert_not_called()

    def test_external_unicode_labels_are_clipped_by_cells_and_controls_removed(self):
        text = tui.fit_text('网络无线网络\n\x1b[31m', 10, pad=True)
        self.assertEqual(tui.cell_width(text), 10)
        self.assertNotIn('\n', text)
        self.assertNotIn('\x1b', text)
        canvas = Canvas(2, 10)
        tui.safe_addstr(canvas, 0, 4, '网络无线网络')
        self.assertLessEqual(tui.cell_width(canvas.writes[0][2]), 6)

    def test_client_selection_scrolls_and_survives_reordering_by_mac(self):
        self.state.hotspot_active = True
        self.state.clients = [{'mac': f'02:00:00:00:00:{i:02x}', 'hostname': f'equipo-{i}'} for i in range(40)]
        self.state.focus = tui.FOCUS_CLIENTS
        self.state.client_index = 39
        canvas = Canvas(24, 80)
        render_panel(canvas, self.state)
        self.assertIn('equipo-39', canvas.text())
        self.assertTrue(any(r.get('index') == 39 and r['type'] == 'client' for r in self.state.mouse_regions))
        reordered = list(reversed(self.state.clients))
        tui.apply_snapshot(self.state, {'clients': reordered})
        self.assertEqual(self.state.client_index, 0)
        tui.handle_keypress(tui.curses.KEY_END, self.state, 80)
        self.assertEqual(self.state.client_index, 39)
        tui.handle_keypress(tui.curses.KEY_PPAGE, self.state, 80)
        self.assertLess(self.state.client_index, 39)

    def test_stale_snapshot_and_error_cannot_replace_state_after_an_action(self):
        tasks = self.tasks()
        tasks.results.put(('poll', 0, {'ssid': 'obsolete'}, None))
        tasks.results.put(('poll_error', 0, 'obsolete failure', None))
        tasks.submit(lambda: None, lambda _: None)
        self.state.ssid = 'current'
        tui.drain_tasks(self.state)
        self.assertEqual(self.state.ssid, 'current')
        self.assertEqual(self.state.poll_error, '')

    def test_poll_failure_keeps_last_sample_and_marks_it_stale(self):
        tasks = self.tasks()
        self.state.hotspot_active = True
        self.state.last_refresh = time.monotonic()
        tasks.results.put(('poll_error', tasks.generation, 'NetworkManager timeout', None))
        tui.drain_tasks(self.state)
        self.assertTrue(self.state.hotspot_active)
        canvas = Canvas()
        render_panel(canvas, self.state)
        self.assertIn('DATOS SIN ACTUALIZAR', canvas.text())
        self.assertIn('NetworkManager timeout', canvas.text())

    def test_diagnostic_completion_never_reopens_or_replaces_a_closed_dialog(self):
        tasks = self.tasks()
        tui.modal_task(self.state, tui.MODAL_DNS, 'Comparando DNS', lambda: {'results': [1]})
        operation, callback = tasks.jobs.get_nowait()
        tui.close_modal(self.state)
        self.state.modal, self.state.modal_data = tui.MODAL_HELP, {'sentinel': True}
        tasks.results.put(('job', None, operation(), callback))
        tui.drain_tasks(self.state)
        self.assertEqual(self.state.modal, tui.MODAL_HELP)
        self.assertEqual(self.state.modal_data, {'sentinel': True})
        self.assertFalse(self.state.busy_label)

    def test_action_failure_is_visible_and_dialog_can_close(self):
        tasks = self.tasks()
        tui.modal_task(self.state, tui.MODAL_DNS, 'Comparando DNS', lambda: None)
        tasks.results.put(('job', 'dig: timeout', None, lambda _: None))
        tui.drain_tasks(self.state)
        self.assertFalse(self.state.modal_data['running'])
        self.assertIn('dig: timeout', self.state.status_msg)
        tui.handle_keypress(27, self.state, 80)
        self.assertIsNone(self.state.modal)

    def test_quit_waits_for_mutation_and_duplicate_action_is_not_queued(self):
        tasks = self.tasks()
        tui.submit_action(self.state, 'Aplicando DNS', lambda: 'Done')
        self.assertFalse(tui.submit_action(self.state, 'Duplicado', lambda: 'Bad'))
        self.assertEqual(tasks.jobs.qsize(), 1)
        tui.execute_button_action('quit', self.state)
        self.assertTrue(self.state.running)
        self.assertTrue(self.state.exit_requested)
        operation, callback = tasks.jobs.get_nowait()
        tasks.results.put(('job', None, operation(), callback))
        tui.drain_tasks(self.state)
        self.assertFalse(self.state.running)

    def test_slow_network_poll_does_not_delay_quitting_or_initial_draw(self):
        entered, release = threading.Event(), threading.Event()
        def slow_poll():
            entered.set()
            release.wait(2)
            return {}
        class Window(Canvas):
            def timeout(self, value):
                self.delay = value
            def keypad(self, value):
                pass
            def erase(self):
                pass
            def noutrefresh(self):
                pass
            def getch(self):
                self.assertion = entered.wait(1)
                return ord('q')
        canvas = Window()
        try:
            with patch.object(tui, 'make_poller', return_value=slow_poll), \
                 patch.object(tui, 'init_colors'), patch.object(tui.curses, 'curs_set'), \
                 patch.object(tui.curses, 'mousemask'), patch.object(tui.curses, 'mouseinterval'), \
                 patch.object(tui.curses, 'set_escdelay'), patch.object(tui.curses, 'doupdate'):
                tui.run_tui(canvas)
            self.assertTrue(canvas.assertion)
            self.assertFalse(release.is_set())
            self.assertIn('thinkISP', canvas.text())
            self.assertLessEqual(canvas.delay, 100)
        finally:
            release.set()

    def test_qr_redraw_is_pure_and_never_crops_the_code(self):
        self.state.modal = tui.MODAL_QR
        self.state.modal_data = {'ssid': 'Demo', 'psk': 'private-example', 'qr_lines': ['█' * 30] * 30}
        canvas = Canvas(24, 80)
        with patch.object(tui, 'run_cmd') as command, patch.object(tui, 'hotspot_details') as details:
            tui.render_qr_modal(canvas, self.state, 24, 80)
            tui.render_qr_modal(canvas, self.state, 24, 80)
        command.assert_not_called()
        details.assert_not_called()
        self.assertIn('Amplía la terminal', canvas.text())
        self.assertNotIn('█', canvas.text())
        tui.close_modal(self.state)
        self.assertEqual(self.state.modal_data, {})

    def test_blocked_associated_client_can_be_unblocked_from_details(self):
        self.state.modal = tui.MODAL_KICK
        self.state.modal_data = {'target_client': {'mac': '02:00:00:00:00:01', 'blocked': True}}
        with patch.object(tui, 'unkick_client', return_value=(True, 'Acceso permitido')) as allow, \
             patch.object(tui, 'kick_client') as deny:
            tui.handle_keypress(10, self.state, 80)
        allow.assert_called_once_with('02:00:00:00:00:01')
        deny.assert_not_called()

    def test_mouse_selects_limit_without_applying_until_confirmed(self):
        self.state.modal = tui.MODAL_LIMIT
        self.state.modal_data = {'preset_idx': 2}
        tui.render_limit_modal(Canvas(), self.state, 30, 120)
        choice = next(r for r in self.state.modal_regions if r.get('value') == 4)
        confirm = next(r for r in self.state.modal_regions if r.get('key') == 10)
        with patch.object(tui, 'set_rate_limit', return_value=(True, '100 Mbps aplicado')) as limit:
            tui.handle_mouse(choice['rect'][0], choice['rect'][1], self.state)
            self.assertEqual(self.state.modal_data['preset_idx'], 4)
            limit.assert_not_called()
            tui.handle_mouse(confirm['rect'][0], confirm['rect'][1], self.state)
        limit.assert_called_once_with('100')
        self.assertIsNone(self.state.modal)

    def test_qr_preparation_preserves_the_complete_quiet_zone(self):
        data = {'ssid': 'Demo', 'psk': 'private-example', 'hidden': False, 'security': 'wpa-psk'}
        with patch.object(tui, 'hotspot_details', return_value=data), \
             patch.object(tui.shutil, 'which', return_value='/usr/bin/qrencode'), \
             patch.object(tui, 'run_cmd', return_value='    \n █▄ \n    ') as command:
            result = tui.prepare_qr()
        self.assertEqual(result['qr_lines'], ['    ', ' █▄ ', '    '])
        self.assertFalse(command.call_args.kwargs['strip'])

    def test_traffic_deltas_accept_zero_baselines_and_ignore_wall_clock_jumps(self):
        self.state._last_rx_bytes = self.state._last_tx_bytes = 0
        self.state._last_poll_time = 10
        values = {
            'is_hotspot_active': True,
            'hotspot_details': {'ssid': 'Demo', 'radio': 'Sin datos', 'security': 'wpa-psk', 'interface': 'wifi-test', 'hidden': False},
            'get_ethernet_status': {}, 'get_hotspot_ip': '192.0.2.1', 'get_ip_forward_status': True,
            'get_gaming_mode': False, 'dns_status': ('Sistema', False), 'get_rate_limit': None,
            'get_timer_status': None, 'get_iface_bytes': (100, 400), 'load_state': {},
            'get_connected_clients': [], 'get_blocked_clients': [],
        }
        with ExitStack() as stack:
            for name, value in values.items():
                stack.enter_context(patch.object(tui, name, return_value=value))
            stack.enter_context(patch.object(tui.time, 'monotonic', return_value=12.5))
            stack.enter_context(patch.object(tui.time, 'time', return_value=-1000))
            tui.update_telemetry(self.state)
        self.assertEqual(self.state.instant_down_bps, 160)
        self.assertEqual(self.state.instant_up_bps, 40)


class TestPanelTasks(unittest.TestCase):
    def test_readonly_measurement_does_not_pause_live_telemetry(self):
        release, entered = threading.Event(), threading.Event()
        def operation():
            entered.set()
            release.wait(2)
        tasks = PanelTasks(lambda: {'live': True}, interval=60)
        self.assertTrue(tasks.submit(operation, lambda _: None, mutation=False))
        tasks.start()
        try:
            self.assertTrue(entered.wait(1))
            event = tasks.results.get(timeout=1)
            self.assertEqual((event[0], event[2]), ('poll', {'live': True}))
            self.assertFalse(release.is_set())
        finally:
            release.set()
            tasks.close()

    def test_job_is_nonblocking_and_callback_runs_only_when_ui_drains(self):
        release, entered, done = threading.Event(), threading.Event(), threading.Event()
        callbacks = []
        def operation():
            entered.set()
            release.wait(2)
            done.set()
            return 'result'
        tasks = PanelTasks(lambda: {}, interval=60)
        tasks.start()
        try:
            self.assertTrue(tasks.submit(operation, lambda result: callbacks.append((result, threading.get_ident()))))
            self.assertTrue(entered.wait(1))
            self.assertFalse(done.is_set())
            self.assertFalse(tasks.submit(operation, lambda _: None))
            release.set()
            # Wait on the actual completion queue, without sleep-based timing assertions.
            while True:
                event = tasks.results.get(timeout=2)
                if event[0] == 'job':
                    break
            self.assertEqual(callbacks, [])
            kind, error, value, callback = event
            callback(value)
            tasks.finish_job()
            self.assertEqual(callbacks, [('result', threading.get_ident())])
        finally:
            release.set()
            tasks.close()

    def test_poll_exceptions_are_reported_instead_of_killing_the_worker(self):
        tasks = PanelTasks(lambda: (_ for _ in ()).throw(RuntimeError('poll failed')))
        tasks.start()
        try:
            kind, generation, error, _ = tasks.results.get(timeout=2)
            self.assertEqual((kind, error), ('poll_error', 'poll failed'))
        finally:
            tasks.close()


if __name__ == '__main__':
    unittest.main()
