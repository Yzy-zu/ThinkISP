"""QoS: verificar errores y colas antes de registrar éxito."""
from contextlib import ExitStack, nullcontext
import unittest
from unittest.mock import patch

from thinkisp.core import qos
from thinkisp.core.network import CommandError, ThinkISPError


class TestQos(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.active = stack.enter_context(patch.object(qos, 'is_hotspot_active', return_value=False))
        self.run = stack.enter_context(patch.object(qos, 'run_cmd', return_value=''))
        self.state = {'unrelated': 'preserved'}
        stack.enter_context(patch.object(qos, 'load_state', side_effect=lambda: dict(self.state)))
        stack.enter_context(patch.object(qos, 'state_lock', side_effect=lambda: nullcontext(dict(self.state))))
        self.save = stack.enter_context(patch.object(qos, 'save_state'))
        self.notify = stack.enter_context(patch.object(qos, 'notify'))
        stack.enter_context(patch.object(qos, 'apply_radio_settings'))
        stack.enter_context(patch.object(qos, 'require_hotspot_profile', return_value='uuid-test'))
        stack.enter_context(patch.object(qos, 'WLAN_IFACE', 'wifi-test'))

    def test_gaming_saves_configuration_for_inactive_hotspot(self):
        self.state['rate_limit'] = '20 Mbps'
        ok, message = qos.set_gaming_mode(True)
        self.assertTrue(ok)
        self.assertIn('próximo encendido', message)
        saved = self.save.call_args.args[0]
        self.assertTrue(saved['gaming_mode'])
        self.assertNotIn('rate_limit', saved)
        self.assertEqual(saved['unrelated'], 'preserved')

    def test_all_mutation_failures_prevent_state_and_notifications(self):
        self.run.side_effect = CommandError(['nmcli'], 'Not authorized', 4)
        for function, args in [(qos.set_gaming_mode, (True,)), (qos.set_gaming_mode, (False,)),
                               (qos.set_rate_limit, (25,)), (qos.clear_rate_limit, ()), (qos.apply_boost_mode, ())]:
            with self.subTest(operation=function.__name__, args=args):
                with self.assertRaises(CommandError):
                    function(*args)
                self.save.assert_not_called()
                self.notify.assert_not_called()

    def test_reactivation_failure_does_not_report_applied_configuration(self):
        self.active.return_value = True
        self.run.side_effect = ['', CommandError(['nmcli'], 'Activation failed')]
        with self.assertRaises(CommandError):
            qos.set_gaming_mode(True)
        self.save.assert_not_called()
        self.notify.assert_not_called()

    def test_actual_queue_is_verified_before_success(self):
        self.active.return_value = True
        self.run.side_effect = ['', '', 'qdisc fq_codel 0: root refcnt 2']
        with self.assertRaisesRegex(ThinkISPError, 'no se encontró'):
            qos.set_gaming_mode(True)
        self.save.assert_not_called()

    def test_gaming_can_be_applied_when_cake_is_observed(self):
        self.active.return_value = True
        self.run.side_effect = ['', '', 'qdisc cake 0: root refcnt 2 bandwidth unlimited']
        self.assertTrue(qos.enable_gaming_mode()[0])
        self.assertTrue(self.save.call_args.args[0]['gaming_mode'])

    def test_rate_limit_replaces_gaming_state(self):
        self.state['gaming_mode'] = True
        self.assertTrue(qos.apply_rate_limit(25)[0])
        state = self.save.call_args.args[0]
        self.assertFalse(state['gaming_mode'])
        self.assertEqual(state['rate_limit'], '25 Mbps')

    def test_invalid_rates_never_reach_networkmanager(self):
        for rate in [0, -1, float('nan'), float('inf'), 'invalid']:
            with self.subTest(rate=rate), self.assertRaises(ThinkISPError):
                qos.set_rate_limit(rate)
        self.run.assert_not_called()

    def test_clear_rate_limit_updates_both_flags(self):
        self.state.update({'gaming_mode': True, 'rate_limit': '25 Mbps'})
        qos.clear_rate_limit()
        state = self.save.call_args.args[0]
        self.assertFalse(state['gaming_mode'])
        self.assertNotIn('rate_limit', state)

    def test_boost_keeps_user_radio_and_does_not_change_cpu(self):
        qos.apply_boost_mode()
        args = self.run.call_args.args[0]
        self.assertNotIn('802-11-wireless.band', args)
        self.assertNotIn('802-11-wireless.channel', args)
        self.assertNotIn('txpower', args)
        self.assertIn('tc.qdiscs', args)
        self.assertTrue(self.run.call_args.kwargs['check'])

    def test_get_current_qos(self):
        self.state.update({'gaming_mode': True, 'rate_limit': '25 Mbps'})
        self.assertEqual(qos.get_current_qos(), {'gaming_mode': True, 'rate_limit': '25 Mbps'})
        self.assertTrue(qos.get_gaming_mode())
        self.assertEqual(qos.get_rate_limit(), '25 Mbps')


if __name__ == '__main__':
    unittest.main()
