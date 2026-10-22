"""Portal CLI/UI contracts; no network or system mutations."""
import contextlib
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from thinkisp.errors import ThinkISPError
from thinkisp.ui import cli, portal as ui
from thinkisp.core import portal

OWNER = '11111111-1111-1111-1111-111111111111'
REPORT = dict(enabled=True, engine='builtin', phase='running',
              url='http://192.0.2.1:49181/', diagnostic=None,
              clients=[dict(ip='192.0.2.2', mac='02:11:22:33:44:55', authorized=True),
                       dict(ip='192.0.2.3', mac='02:11:22:33:44:56', authorized=False)])


class TestPortalCLI(unittest.TestCase):
    def run_cli(self, action, **kwargs):
        output = io.StringIO()
        with patch('thinkisp.core.profile.profile_uuid', return_value=OWNER), contextlib.redirect_stdout(output):
            cli.cmd_portal(SimpleNamespace(portal_action=action, **kwargs))
        return output.getvalue()

    def test_json_reports_actual_backend_schema(self):
        with patch.object(portal, 'status', return_value=REPORT) as status:
            self.assertEqual(json.loads(self.run_cli('status', json=True)), REPORT)
        status.assert_called_once_with(OWNER)

    def test_text_counts_only_confirmed_authorizations(self):
        with patch.object(portal, 'status', return_value=REPORT):
            text = self.run_cli('status', json=False)
        self.assertIn('Clientes autorizados: 1', text)
        self.assertIn('192.0.2.2', text)
        self.assertNotIn('192.0.2.3', text)

    def test_enable_failure_never_prints_success(self):
        with patch.object(portal, 'configure', side_effect=ThinkISPError('AP activo')):
            with self.assertRaisesRegex(ThinkISPError, 'AP activo'):
                self.run_cli('enable')

    def test_rejected_authorization_is_an_error(self):
        with patch.object(portal, 'authorize', return_value=(False, 'Vecino desconocido')):
            with self.assertRaisesRegex(ThinkISPError, 'Vecino desconocido'):
                self.run_cli('authorize', target='192.0.2.9')


class TestPortalUI(unittest.TestCase):
    def panel(self, choices, active=False):
        view = Mock()
        view.choose.side_effect = choices
        view.work.side_effect = lambda title, callback: callback()
        backend = Mock()
        backend.status.return_value = REPORT
        return view, backend, contextlib.ExitStack()

    def test_authorized_view_uses_confirmed_backend_clients(self):
        view, backend, stack = self.panel(['authorized', 'back'])
        with stack:
            stack.enter_context(patch.object(ui, 'TerminalUI', return_value=view))
            stack.enter_context(patch.object(ui, 'profile_uuid', return_value=OWNER))
            stack.enter_context(patch.object(ui, 'is_hotspot_active', return_value=True))
            stack.enter_context(patch.object(ui, 'get_portal_backend', return_value=backend))
            ui.manage_portal(object())
        text = view.message.call_args.args[1]
        self.assertIn('192.0.2.2', text)
        self.assertNotIn('192.0.2.3', text)
        backend.authorize.assert_not_called()

    def test_active_ap_blocks_configuration(self):
        view, backend, stack = self.panel(['toggle', 'back'])
        with stack:
            stack.enter_context(patch.object(ui, 'TerminalUI', return_value=view))
            stack.enter_context(patch.object(ui, 'profile_uuid', return_value=OWNER))
            stack.enter_context(patch.object(ui, 'is_hotspot_active', return_value=True))
            stack.enter_context(patch.object(ui, 'get_portal_backend', return_value=backend))
            ui.manage_portal(object())
        backend.configure.assert_not_called()
        self.assertIn('apagarlo', view.message.call_args.args[1])

    def test_failed_revoke_displays_error(self):
        view, backend, stack = self.panel(['revoke', '02:11:22:33:44:55', 'back'])
        backend.revoke.return_value = (False, 'Supervisor no disponible')
        with stack:
            stack.enter_context(patch.object(ui, 'TerminalUI', return_value=view))
            stack.enter_context(patch.object(ui, 'profile_uuid', return_value=OWNER))
            stack.enter_context(patch.object(ui, 'is_hotspot_active', return_value=True))
            stack.enter_context(patch.object(ui, 'get_portal_backend', return_value=backend))
            ui.manage_portal(object())
        backend.revoke.assert_called_once_with(OWNER, '02:11:22:33:44:55')
        view.message.assert_called_once_with('Error', 'Supervisor no disponible')
