"""DNS: modelo de NM con perfiles guardados y DNS activos independientes."""
import copy
from contextlib import ExitStack, contextmanager
import unittest
from unittest.mock import patch

from thinkisp.core import dns_state as dns
from thinkisp.core.network import CommandError, ThinkISPError


ORIGINAL = {'ipv4.dns': '192.0.2.53,192.0.2.54', 'ipv4.ignore-auto-dns': 'yes',
            'ipv6.dns': '2001:db8::53', 'ipv6.ignore-auto-dns': 'yes'}


class TestDnsTransactions(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = {}
        self.profile = copy.deepcopy(ORIGINAL)
        self.runtime = copy.deepcopy(ORIGINAL)
        self.active = True
        self.live_uuid = 'upstream'
        self.fail_reapply = False
        self.fail_modify = False
        self.events = []
        self.stack.enter_context(patch.object(dns, 'state_lock', self.lock))
        self.save = self.stack.enter_context(patch.object(dns, 'save_state', side_effect=self.persist))
        self.stack.enter_context(patch.object(dns, 'load_state', side_effect=lambda: copy.deepcopy(self.state)))
        self.stack.enter_context(patch.object(dns, 'profile_uuid', return_value='hotspot'))
        self.stack.enter_context(patch.object(dns, 'require_hotspot_profile', return_value='hotspot'))
        self.stack.enter_context(patch.object(dns, 'is_hotspot_active', side_effect=lambda **kw: self.active))
        self.stack.enter_context(patch.object(dns, 'detect_eth_iface', return_value='uplink-test'))
        self.stack.enter_context(patch.object(dns, 'profile_values', side_effect=lambda *a, **kw: copy.deepcopy(self.profile)))
        self.command = self.stack.enter_context(patch.object(dns, 'run_cmd', side_effect=self.run_nm))

    @contextmanager
    def lock(self):
        yield copy.deepcopy(self.state)

    def persist(self, state):
        self.events.append('save')
        self.state = copy.deepcopy(state)

    def run_nm(self, args, **kwargs):
        if args[-2:] == ['show', '--active']:
            return f'Custom uplink:{self.live_uuid}:uplink-test'
        if args[:3] == ['nmcli', 'connection', 'modify']:
            self.events.append('modify')
            self.assertIn('--temporary', args)
            self.assertEqual(args[args.index('uuid') + 1], 'upstream')
            self.assertTrue(self.state['dns_journal']['hotspot']['upstream'])
            if self.fail_modify:
                self.fail_modify = False
                raise CommandError(args, 'Not authorized')
            pairs = args[args.index('uuid') + 2:]
            self.profile.update(dict(zip(pairs[::2], pairs[1::2])))
            return ''
        if args[:3] == ['nmcli', 'device', 'reapply']:
            if self.fail_reapply:
                self.fail_reapply = False
                raise CommandError(args, 'Reapply failed')
            self.runtime = copy.deepcopy(self.profile)
            return ''
        if 'IP4.DNS,IP6.DNS' in args:
            return self.runtime['ipv4.dns'] + '\n' + self.runtime['ipv6.dns']
        raise AssertionError('Unexpected command: ' + str(args))

    def test_manual_ipv4_ipv6_and_flags_are_restored_exactly(self):
        self.assertTrue(dns.configure_dns('adguard')[0])
        self.assertEqual(self.events[:2], ['save', 'modify'])
        self.assertEqual(self.profile['ipv6.ignore-auto-dns'], 'yes')
        self.assertEqual(self.profile['ipv6.dns'], '')
        self.assertTrue(dns.dns_status()[1])
        dns.configure_dns(None)
        self.assertEqual(self.profile, ORIGINAL)
        self.assertEqual(self.runtime, ORIGINAL)
        self.assertEqual(self.state['dns_journal'], {})

    def test_original_snapshot_is_not_overwritten_by_switching_providers(self):
        dns.configure_dns('cloudflare')
        dns.configure_dns('google')
        self.assertEqual(self.state['dns_journal']['hotspot']['upstream']['original'], ORIGINAL)
        dns.restore_dns('hotspot')
        self.assertEqual(self.profile, ORIGINAL)

    def test_adblock_off_returns_to_the_users_previous_provider(self):
        dns.configure_dns('cloudflare')
        dns.configure_dns(adblock=True)
        self.assertTrue(dns.dns_status()[1])
        dns.configure_dns(adblock=False)
        self.assertIn('Cloudflare', dns.dns_status()[0])
        self.assertFalse(dns.dns_status()[1])
        self.assertEqual(self.state['dns_settings']['hotspot']['provider'], 'cloudflare')

    def test_inactive_hotspot_saves_a_preference_without_changing_the_pc_dns(self):
        self.active = False
        self.assertIn('próximo encendido', dns.configure_dns('adguard')[1])
        self.assertEqual(self.profile, ORIGINAL)
        self.assertFalse(self.command.called)
        self.assertFalse(dns.dns_status()[1])
        self.active = True
        dns.apply_saved_dns('hotspot')
        self.assertTrue(dns.dns_status()[1])

    def test_reapply_failure_rolls_back_without_setting_a_success_flag(self):
        self.fail_reapply = True
        with self.assertRaisesRegex(CommandError, 'Reapply failed'):
            dns.configure_dns('adguard')
        self.assertEqual(self.profile, ORIGINAL)
        self.assertEqual(self.runtime, ORIGINAL)
        self.assertNotIn('dns_settings', self.state)
        self.assertFalse(self.state['dns_journal']['hotspot'])

    def test_permission_error_preserves_the_original_configuration(self):
        self.fail_modify = True
        with self.assertRaises(CommandError):
            dns.configure_dns('adguard')
        self.assertEqual(self.profile, ORIGINAL)
        self.assertNotIn('dns_settings', self.state)

    def test_external_changes_are_not_overwritten_during_recovery(self):
        dns.configure_dns('adguard')
        self.profile['ipv4.dns'] = '203.0.113.53'
        with self.assertRaisesRegex(ThinkISPError, 'fuera de thinkISP'):
            dns.restore_dns('hotspot')
        self.assertEqual(self.profile['ipv4.dns'], '203.0.113.53')
        self.assertIn('upstream', self.state['dns_journal']['hotspot'])
        self.assertFalse(dns.dns_status()[1])

    def test_new_active_connection_is_not_reapplied_when_restoring_the_old_one(self):
        dns.configure_dns('adguard')
        self.live_uuid = 'different-connection'
        self.command.reset_mock()
        dns.restore_dns('hotspot')
        self.assertEqual(self.profile, ORIGINAL)
        self.assertFalse(any(call.args[0][:3] == ['nmcli', 'device', 'reapply'] for call in self.command.call_args_list))

    def test_journal_recovers_after_process_restart(self):
        dns.configure_dns('adguard')
        journal_on_disk = copy.deepcopy(self.state)
        self.active = False
        self.state = journal_on_disk
        dns.restore_dns('hotspot')
        self.assertEqual(self.profile, ORIGINAL)
        self.assertEqual(self.runtime, ORIGINAL)
        self.assertEqual(self.state['dns_journal'], {})
        self.assertEqual(self.state['dns_settings']['hotspot']['provider'], 'adguard')

    def test_live_dns_mismatch_is_not_reported_as_adblock_applied(self):
        dns.configure_dns('adguard')
        self.runtime['ipv4.dns'] = '203.0.113.53'
        label, enabled = dns.dns_status()
        self.assertFalse(enabled)
        self.assertIn('sin aplicar', label)


if __name__ == '__main__':
    unittest.main()
