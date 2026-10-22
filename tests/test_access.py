"""Control de acceso: reglas reales serializadas, verificación y recuperación."""
import copy
from contextlib import ExitStack, contextmanager
import json
import unittest
from unittest.mock import patch

from thinkisp.core import access
from thinkisp.core.network import CommandError, ThinkISPError


MAC = '02:00:00:00:00:01'
OTHER = '02:00:00:00:00:02'


class Kernel:
    def __init__(self):
        self.tables = {}
        self.calls = []

    def run(self, args, payload=None):
        self.calls.append((args, payload))
        if args == ['-j', 'list', 'tables']:
            return json.dumps({'nftables': [{'table': {'family': 'inet', 'name': 'external-firewall'}}] +
                              [{'table': objects[0]['table']} for objects in self.tables.values()]})
        if args[:4] == ['-j', 'list', 'table', 'netdev']:
            return json.dumps({'nftables': self.tables[args[4]]})
        commands = json.loads(payload)['nftables']
        # Atomic batch, como nft: no modificar el estado hasta aceptar el lote.
        tables = copy.deepcopy(self.tables)
        for command in commands:
            if 'delete' in command:
                del tables[command['delete']['table']['name']]
                continue
            value = command['add']
            if 'table' in value:
                tables[value['table']['name']] = [value]
            else:
                obj = next(iter(value.values()))
                tables[obj['table']].append(value)
        self.tables = tables
        return ''


class TestFirewall(unittest.TestCase):
    def setUp(self):
        self.kernel = Kernel()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(access.shutil, 'which', return_value='/usr/bin/nft'))
        self.firewall = access.Firewall('uuid-profile', 'wifi-test')
        self.stack.enter_context(patch.object(self.firewall, 'run', side_effect=self.kernel.run))

    def test_policy_is_scoped_to_one_interface_and_checks_the_mac_set(self):
        self.firewall.apply([MAC])
        objects = self.kernel.tables[self.firewall.table]
        chain = next(item['chain'] for item in objects if 'chain' in item)
        rule = next(item['rule'] for item in objects if 'rule' in item)
        self.assertEqual(chain['family'], 'netdev')
        self.assertEqual(chain['dev'], 'wifi-test')
        self.assertEqual(chain['hook'], 'ingress')
        self.assertEqual(rule['expr'][-1], {'drop': None})
        self.assertNotIn('flush', json.dumps(self.kernel.calls))

    def test_add_remove_and_clear_only_touch_the_owned_table(self):
        self.firewall.apply([MAC])
        self.firewall.apply([MAC, OTHER])
        self.firewall.apply([OTHER])
        self.firewall.apply([])
        self.assertEqual(self.kernel.tables, {})
        for args, payload in self.kernel.calls:
            if payload:
                for command in json.loads(payload)['nftables']:
                    for obj in command.get('delete', {}).values():
                        self.assertEqual(obj['family'], 'netdev')
                        self.assertEqual(obj['name'], self.firewall.table)

    def test_foreign_table_is_not_replaced(self):
        self.kernel.tables[self.firewall.table] = [{'table': {'family': 'netdev', 'name': self.firewall.table, 'comment': 'foreign'}}]
        with self.assertRaisesRegex(ThinkISPError, 'propia'):
            self.firewall.apply([MAC])
        self.assertFalse(any(payload for _, payload in self.kernel.calls))

    def test_incorrect_kernel_state_is_rejected(self):
        self.firewall.apply([MAC])
        data = json.loads(self.kernel.run(['-j', 'list', 'table', 'netdev', self.firewall.table]))
        for item in data['nftables']:
            if 'chain' in item:
                item['chain']['dev'] = 'different-device'
        with self.assertRaisesRegex(ThinkISPError, 'no coinciden'):
            self.firewall.verify(data, [MAC])

    def test_permission_failure_is_not_confused_with_no_rules(self):
        with patch.object(self.firewall, 'run', side_effect=CommandError(['nft'], 'Operation not permitted')):
            with self.assertRaises(CommandError):
                self.firewall.read()

    def test_names_are_distinct_for_profiles(self):
        self.assertNotEqual(access.table_name('profile-one'), access.table_name('profile-two'))


class TestBlockPersistence(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.state = {}
        self.active = True
        self.kernel = Kernel()
        self.stack.enter_context(patch.object(access, 'WLAN_IFACE', 'wifi-test'))
        self.stack.enter_context(patch.object(access, 'require_hotspot_profile', return_value='profile'))
        self.stack.enter_context(patch.object(access, 'is_hotspot_active', side_effect=lambda **kw: self.active))
        self.stack.enter_context(patch.object(access, 'load_state', side_effect=lambda: copy.deepcopy(self.state)))
        self.stack.enter_context(patch.object(access, 'state_lock', self.lock))
        self.save = self.stack.enter_context(patch.object(access, 'save_state', side_effect=self.persist))
        self.notify = self.stack.enter_context(patch.object(access, 'notify'))
        self.stack.enter_context(patch.object(access.shutil, 'which', return_value='/usr/bin/nft'))
        self.stack.enter_context(patch.object(access.Firewall, 'run', side_effect=self.kernel.run))

    @contextmanager
    def lock(self):
        yield copy.deepcopy(self.state)

    def persist(self, state):
        self.state = copy.deepcopy(state)

    def test_block_is_saved_only_after_kernel_verification_and_can_be_removed(self):
        ok, message = access.set_client_block(MAC)
        self.assertTrue(ok)
        self.assertIn('Internet', message)
        self.assertEqual(access.blocked_clients(), [MAC])
        access.set_client_block(MAC, False)
        self.assertEqual(access.blocked_clients(), [])
        self.assertFalse(self.kernel.tables)

    def test_offline_preference_is_not_reported_as_already_applied(self):
        self.active = False
        ok, message = access.set_client_block(MAC)
        self.assertTrue(ok)
        self.assertIn('próximo encendido', message)
        self.assertEqual(self.kernel.calls, [])
        self.active = True
        access.apply_saved_blocks('profile')
        self.assertTrue(self.kernel.tables)

    def test_persistence_failure_rolls_back_the_firewall(self):
        self.save.side_effect = ThinkISPError('disk full')
        with self.assertRaisesRegex(ThinkISPError, 'disk full'):
            access.set_client_block(MAC)
        self.assertEqual(self.state, {})
        self.assertFalse(self.kernel.tables)
        self.notify.assert_not_called()

    def test_missing_nft_does_not_save_a_successful_block(self):
        with patch.object(access.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(ThinkISPError, 'nftables'):
                access.set_client_block(MAC)
        self.assertEqual(self.state, {})
        self.notify.assert_not_called()

    def test_mac_validation_rejects_groups_and_injection(self):
        for value in ['ff:ff:ff:ff:ff:ff', '11:22:33:44:55:66', '00:00:00:00:00:00', MAC + '; flush ruleset']:
            with self.subTest(value=value), self.assertRaises(ThinkISPError):
                access.set_client_block(value)
        self.assertEqual(self.kernel.calls, [])

    def test_ip_resolution_is_limited_to_the_hotspot_neighbors(self):
        output = json.dumps([{'dst': '192.0.2.10', 'lladdr': MAC}])
        with patch.object(access, 'run_cmd', return_value=output) as command:
            self.assertEqual(access.resolve_target('192.0.2.10'), MAC)
        self.assertEqual(command.call_args.args[0][-2:], ['dev', 'wifi-test'])

    def test_stop_clears_applied_rules_but_retains_the_preference(self):
        access.set_client_block(MAC)
        access.clear_applied_blocks('profile')
        self.assertFalse(self.kernel.tables)
        self.assertEqual(access.blocked_clients(), [MAC])

    def test_repeated_cleanup_does_not_require_firewall_authorization(self):
        access.set_client_block(MAC)
        access.clear_applied_blocks('profile')
        self.kernel.calls.clear()
        access.clear_applied_blocks('profile')
        self.assertEqual(self.kernel.calls, [])

    def test_orphaned_journal_recovers_rules_without_losing_saved_preferences(self):
        access.set_client_block(MAC)
        self.state.pop('access_active')
        self.state['access_journal'] = {'profile': {'previous': [], 'interface': 'wifi-test'}}
        access.clear_applied_blocks('profile')
        self.assertFalse(self.kernel.tables)
        self.assertEqual(access.blocked_clients(), [MAC])
        self.assertNotIn('profile', self.state['access_journal'])


class TestAuthentication(unittest.TestCase):
    def test_background_cleanup_does_not_prompt_for_authentication(self):
        with patch.object(access.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), \
             patch.object(access.os, 'geteuid', return_value=1000), \
             patch.dict(access.os.environ, {'THINKISP_NO_AUTH': '1'}), \
             patch.object(access, 'run_cmd', side_effect=CommandError(['nft'], 'Operation not permitted')) as command:
            with self.assertRaisesRegex(ThinkISPError, 'PolicyKit'):
                access.Firewall('profile', 'wifi-test').run(['-j', 'list', 'tables'])
        self.assertEqual(command.call_count, 1)

    def test_policykit_is_used_only_after_permission_denial(self):
        with patch.object(access.shutil, 'which', side_effect=lambda name: '/usr/bin/' + name), \
             patch.object(access.os, 'geteuid', return_value=1000), \
             patch.object(access, 'run_cmd', side_effect=[CommandError(['nft'], 'Operation not permitted'), '{}']) as command:
            firewall = access.Firewall('profile', 'wifi-test')
            self.assertEqual(firewall.run(['-j', 'list', 'tables']), '{}')
        self.assertEqual(command.call_args.args[0][:2], ['/usr/bin/pkexec', '/usr/bin/nft'])


if __name__ == '__main__':
    unittest.main()
