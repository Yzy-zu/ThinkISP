"""Regresiones del primer perfil, capacidades, secretos y aplicación de radio."""
from contextlib import ExitStack, nullcontext
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from thinkisp import config
from thinkisp.core import network, profile
from thinkisp.core.network import CommandError, ThinkISPError


RADIO = """Wiphy phy7
    Supported interface modes:
         * managed
         * AP
    HT20/HT40
    VHT Capabilities (0x1234):
    Frequencies:
        * 2412 MHz [1] (20.0 dBm)
        * 2437 MHz [6] (17.0 dBm)
        * 5180 MHz [36] (23.0 dBm)
        * 5200 MHz [40] (disabled)
        * 5260 MHz [52] (23.0 dBm) (no IR, radar detection)
"""


def capabilities():
    return profile.Capabilities('wifi-test', 'phy7', 'driver-test', profile.parse_channels(RADIO),
                                widths=['auto', '20', '40', '80'])


def settings():
    return {'802-11-wireless.ssid': 'Red de pruebas', '802-11-wireless-security.key-mgmt': 'wpa-psk',
            '802-11-wireless-security.psk': 'ClaveDePruebaUnica!', '802-11-wireless.band': '',
            '802-11-wireless.channel': '0', '802-11-wireless.hidden': 'no'}


class TestCapabilities(unittest.TestCase):
    def test_other_radio_bands_are_not_mislabeled_as_6ghz(self):
        caps = profile.Capabilities('wifi-test', 'phy7', 'driver-test',
                                    [profile.Channel(1, 58320, 10), profile.Channel(1, 915, 10)])
        self.assertEqual(caps.bands, [])

    def test_regulatory_restrictions_and_channel_power_are_parsed(self):
        caps = capabilities()
        self.assertEqual(caps.bands, ['a', 'bg'])
        self.assertEqual([c.number for c in caps.channels if c.usable], [1, 6, 36])
        self.assertEqual(caps.channels[1].max_dbm, 17)

    def test_rejects_prohibited_channels_wrong_bands_and_excess_power(self):
        for change, power in [({'802-11-wireless.band': 'a', '802-11-wireless.channel': '52'}, None),
                              ({'802-11-wireless.band': 'a', '802-11-wireless.channel': '40'}, None),
                              ({'802-11-wireless.band': 'bg', '802-11-wireless.channel': '36'}, None),
                              ({'802-11-wireless.channel': '6'}, None), ({}, 18), ({}, float('nan'))]:
            candidate = settings()
            candidate.update(change)
            with self.subTest(change=change, power=power), self.assertRaises(ThinkISPError):
                profile.validate_settings(candidate, capabilities(), power)

    def test_valid_channel_allows_its_own_power_limit(self):
        candidate = settings()
        candidate.update({'802-11-wireless.band': 'a', '802-11-wireless.channel': '36'})
        profile.validate_settings(candidate, capabilities(), 23)

    def test_rejects_invalid_credentials_and_profile_role(self):
        for change in [{'802-11-wireless.ssid': 'á' * 17}, {'802-11-wireless.ssid': 'red\ninyectada'},
                       {'802-11-wireless-security.psk': 'short'}, {'802-11-wireless.mode': 'infrastructure'},
                       {'ipv4.method': 'manual'}, {'802-11-wireless-security.key-mgmt': 'sae'}]:
            candidate = settings()
            candidate.update(change)
            with self.subTest(change=list(change)), self.assertRaises(ThinkISPError):
                profile.validate_settings(candidate, capabilities())

    def test_no_ap_support_is_error_before_any_changes(self):
        with patch.object(profile, 'WLAN_IFACE', 'wifi-test'), patch.object(profile, 'run_cmd', return_value='no') as run:
            with self.assertRaisesRegex(ThinkISPError, 'soporte AP'):
                profile.inspect_capabilities()
        self.assertEqual(run.call_count, 1)

    def test_widths_require_both_driver_and_networkmanager(self):
        def run(args, **kwargs):
            if 'WIFI-PROPERTIES.AP' in args:
                return 'yes'
            if args[:4] == ['iw', 'dev', 'wifi-test', 'info']:
                return 'Interface wifi-test\n wiphy 7'
            if args[:3] == ['iw', 'phy', 'phy7']:
                return RADIO
            if args[:3] == ['iw', 'reg', 'get']:
                return 'country XX: DFS-UNSET'
            return ''
        def supported(template, prop, value):
            return prop != '802-11-wireless.channel-width' or value != '80'
        with patch.object(profile, 'WLAN_IFACE', 'wifi-test'), patch.object(profile, 'run_cmd', side_effect=run), \
             patch.object(profile, '_offline_template', return_value='keyfile'), patch.object(profile, 'probe_property', side_effect=supported):
            caps = profile.inspect_capabilities()
        self.assertEqual(caps.widths, ['auto', '20', '40'])
        self.assertNotIn('80', caps.widths)


class TestProfiles(unittest.TestCase):
    def setUp(self):
        selected = patch.object(profile, 'state_lock', side_effect=lambda: nullcontext(profile.load_state()))
        selected.start()
        self.addCleanup(selected.stop)

    def test_missing_profile_is_distinct_from_nmcli_failure(self):
        with patch.object(profile, 'run_cmd', return_value='Other:uuid-other'):
            self.assertIsNone(profile.profile_uuid())
        with patch.object(profile, 'run_cmd', side_effect=CommandError(['nmcli'], 'NetworkManager unavailable')):
            with self.assertRaises(CommandError):
                profile.profile_uuid()

    def test_escaped_profile_names_and_duplicates(self):
        with patch.object(profile, 'HOTSPOT_CONN', 'Mi:red'), patch.object(profile, 'run_cmd', return_value=r'Mi\:red:uuid-ok'):
            self.assertEqual(profile.profile_uuid(), 'uuid-ok')
        with patch.object(profile, 'HOTSPOT_CONN', 'Mi:red'), patch.object(profile, 'run_cmd', return_value='Mi\\:red:a\nMi\\:red:b'):
            with self.assertRaises(ThinkISPError):
                profile.profile_uuid()

    def test_first_save_creates_secure_shared_ap_without_forcing_radio(self):
        with patch.object(profile, 'profile_uuid', return_value=None), patch.object(profile, '_offline_template', return_value='keyfile'), \
             patch.object(profile, 'run_cmd', return_value='') as run, patch.object(profile, 'load_state', return_value={}), \
             patch.object(profile, 'save_state') as save:
            result = profile.save_profile(settings(), capabilities())
        commands = [call.args[0] for call in run.call_args_list]
        create = next(args for args in commands if args[:3] == ['nmcli', 'connection', 'add'])
        self.assertEqual(create[create.index('802-11-wireless.mode') + 1], 'ap')
        self.assertEqual(create[create.index('ipv4.method') + 1], 'shared')
        self.assertEqual(create[create.index('802-11-wireless.channel') + 1], '')
        self.assertNotIn('802-11-wireless.tx-power', create)
        self.assertNotIn('ipv4.addresses', create)
        saved = save.call_args.args[0]
        self.assertIn(result, saved['radio'])
        self.assertNotIn('ClaveDePruebaUnica!', str(saved))

    def test_existing_profile_is_modified_by_uuid_and_credentials_preserved(self):
        candidate = settings()
        with patch.object(profile, 'profile_uuid', return_value='existing-uuid'), \
             patch.object(profile, 'profile_values', return_value={'connection.type': '802-11-wireless', '802-11-wireless.mode': 'ap'}), \
             patch.object(profile, '_offline_template', return_value='keyfile'), patch.object(profile, 'run_cmd', return_value='') as run, \
             patch.object(profile, 'load_state', return_value={}), patch.object(profile, 'save_state'):
            self.assertEqual(profile.save_profile(candidate, capabilities()), 'existing-uuid')
        self.assertEqual(run.call_args.args[0][:5], ['nmcli', 'connection', 'modify', 'uuid', 'existing-uuid'])
        self.assertIn(candidate['802-11-wireless-security.psk'], run.call_args.args[0])

    def test_existing_non_ap_is_never_overwritten(self):
        with patch.object(profile, 'profile_uuid', return_value='existing-uuid'), \
             patch.object(profile, 'profile_values', return_value={'connection.type': '802-11-wireless', '802-11-wireless.mode': 'infrastructure'}), \
             patch.object(profile, '_offline_template', return_value='keyfile'), patch.object(profile, 'run_cmd', return_value='') as run:
            with self.assertRaisesRegex(ThinkISPError, 'otro tipo'):
                profile.save_profile(settings(), capabilities())
        self.assertTrue(all('--offline' in call.args[0] for call in run.call_args_list))

    def test_failed_creation_never_saves_success_state(self):
        def run(args, **kwargs):
            if '--offline' not in args:
                raise CommandError(args, 'Permission denied', 4)
            return ''
        with patch.object(profile, 'profile_uuid', return_value=None), patch.object(profile, '_offline_template', return_value='keyfile'), \
             patch.object(profile, 'run_cmd', side_effect=run), patch.object(profile, 'save_state') as save:
            with self.assertRaises(CommandError):
                profile.save_profile(settings(), capabilities())
        save.assert_not_called()

    def test_default_radio_does_not_require_privileged_iw_changes(self):
        with patch.object(profile, 'load_state', return_value={'radio': {'uuid': {'tx_power_dbm': None, 'manage_tx_power': False}}}), \
             patch.object(profile, 'run_cmd') as run:
            profile.apply_radio_settings('uuid')
        run.assert_not_called()

    def test_custom_power_converts_dbm_to_mbm_and_checks_failure(self):
        with patch.object(profile, 'WLAN_IFACE', 'wifi-test'), \
             patch.object(profile, 'load_state', return_value={'radio': {'uuid': {'tx_power_dbm': 17.5}}}), \
             patch.object(profile, 'run_cmd', side_effect=CommandError(['iw'], 'Operation not permitted')) as run:
            with self.assertRaises(CommandError):
                profile.apply_radio_settings('uuid')
        self.assertEqual(run.call_args.args[0], ['iw', 'dev', 'wifi-test', 'set', 'txpower', 'limit', '1750'])
        self.assertTrue(run.call_args.kwargs['check'])

    def test_actual_radio_and_credentials_are_returned_without_static_defaults(self):
        values = {'802-11-wireless.ssid': 'Otra red', '802-11-wireless.band': 'bg',
                  '802-11-wireless.channel': '6', '802-11-wireless.hidden': 'yes',
                  '802-11-wireless-security.key-mgmt': 'sae', 'connection.zone': 'custom-zone',
                  '802-11-wireless-security.psk': 'AnotherSecret'}
        with patch.object(profile, 'WLAN_IFACE', 'wifi-test'), patch.object(profile, 'profile_values', return_value=values), \
             patch.object(profile, 'run_cmd', return_value='channel 6 (2437 MHz), width: 20 MHz, center1: 2437 MHz\n txpower 15.00 dBm'):
            actual = profile.hotspot_details(secrets=True)
        self.assertEqual(actual['radio'], '2437 MHz · canal 6 · 20 MHz')
        self.assertEqual(actual['ssid'], 'Otra red')
        self.assertEqual(actual['psk'], 'AnotherSecret')
        self.assertEqual(actual['tx_power'], '15.00 dBm')


class TestCommandFailures(unittest.TestCase):
    def test_corrupt_state_is_reported_instead_of_resetting_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'state.json'
            target.write_text('invalid json')
            with patch.object(network, 'STATE_FILE', target):
                with self.assertRaisesRegex(ThinkISPError, 'leer el estado'):
                    network.load_state()

    def test_required_failures_report_stderr_optional_queries_return_none(self):
        result = subprocess.CompletedProcess(['nmcli'], 4, 'partial', 'Not authorized')
        with patch.object(network.subprocess, 'run', return_value=result):
            self.assertIsNone(network.run_cmd(['nmcli']))
            with self.assertRaisesRegex(CommandError, 'Not authorized') as error:
                network.run_cmd(['nmcli'], check=True)
            self.assertEqual(error.exception.returncode, 4)

    def test_missing_binary_and_timeout_are_actionable_errors(self):
        for error in [FileNotFoundError('nmcli not found'), subprocess.TimeoutExpired(['nmcli'], 2)]:
            with self.subTest(error=type(error).__name__), patch.object(network.subprocess, 'run', side_effect=error):
                with self.assertRaises(CommandError):
                    network.run_cmd(['nmcli'], check=True)

    def test_passwords_are_redacted_from_command_errors(self):
        error = CommandError(['nmcli', '802-11-wireless-security.psk', 'SecretForTest'], 'invalid SecretForTest', 2)
        self.assertNotIn('SecretForTest', str(error))

    def test_state_write_failure_is_visible(self):
        with patch.object(network.tempfile, 'NamedTemporaryFile', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(ThinkISPError, 'guardar el estado'):
                with tempfile.TemporaryDirectory() as directory, patch.object(network, 'STATE_DIR', Path(directory)):
                    network.save_state({'test': True})

    def test_state_file_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'state.json'
            with patch.object(network, 'STATE_DIR', Path(directory)), patch.object(network, 'STATE_FILE', target):
                network.save_state({'test': True})
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class TestOfflineNetworkManager(unittest.TestCase):
    @unittest.skipUnless(shutil.which('nmcli'), 'NetworkManager CLI not installed')
    def test_generated_ap_profile_is_valid_with_installed_nmcli(self):
        template = profile._offline_template()
        self.assertIn('mode=ap', template)
        self.assertIn('method=shared', template)
        self.assertTrue(profile.probe_property(template, '802-11-wireless.hidden', 'yes'))
        # No daemon, permisos administrativos ni conexiones reales en este test.


if __name__ == '__main__':
    unittest.main()
