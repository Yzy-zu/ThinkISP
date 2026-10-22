"""Diferencias de sistema, versiones, rutas e instalación sin tocar la red."""

from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import main
from thinkisp import config
from thinkisp.core import doctor, network, profile
from thinkisp.core.network import CommandError, ThinkISPError
from thinkisp.ui import tui

ROOT = Path(__file__).resolve().parent.parent


class TestInterfaceCompatibility(unittest.TestCase):
    def test_default_routes_use_metrics_instead_of_output_order_or_name_prefix(self):
        routes = json.dumps([{'dev': 'enp9s0', 'metric': 900}, {'dev': 'loft-uplink', 'metric': 10},
                             {'dev': 'wifi-test', 'metric': 1}])
        with patch.dict(os.environ, {}, clear=True), patch.object(config, '_probe', return_value=routes):
            self.assertEqual(config.detect_eth_iface('wifi-test'), 'loft-uplink')

    def test_bridge_and_usb_upstreams_are_valid_default_routes(self):
        for iface in ('br-home', 'usb0', 'bond0', 'wwan0', 'tailscale0'):
            with self.subTest(iface=iface), patch.dict(os.environ, {}, clear=True), \
                 patch.object(config, '_probe', return_value=json.dumps([{'dev': iface}])):
                self.assertEqual(config.detect_eth_iface('wifi-test'), iface)

    def test_old_iproute2_text_output_and_ipv6_only_default_route(self):
        def probe(args):
            if '-j' in args or '-4' in args:
                return ''
            return 'default via fe80::1 dev usb0 metric 600\ndefault via fe80::2 dev bridge0 metric 20'
        with patch.dict(os.environ, {}, clear=True), patch.object(config, '_probe', side_effect=probe):
            self.assertEqual(config.detect_eth_iface('wifi-test'), 'bridge0')

    def test_disconnected_networkmanager_devices_are_not_an_upstream(self):
        def probe(args):
            return 'unplugged:ethernet:disconnected' if 'nmcli' in args else ''
        with patch.dict(os.environ, {}, clear=True), patch.object(config, '_probe', side_effect=probe):
            self.assertIsNone(config.detect_eth_iface('wifi-test'))

    def test_unmanaged_wifi_is_not_preferred_over_managed_ap_adapter(self):
        def probe(args):
            if 'DEVICE,TYPE' in args:
                return 'unmanaged:wifi\nusb-ap:wifi'
            if 'GENERAL.NM-MANAGED' in args:
                return 'no' if args[-1] == 'unmanaged' else 'yes'
            if 'WIFI-PROPERTIES.AP' in args:
                return 'yes'
            return ''
        with patch.dict(os.environ, {}, clear=True), patch.object(config, '_probe', side_effect=probe):
            self.assertEqual(config.detect_wlan_iface(), 'usb-ap')

    def test_upstream_status_refreshes_interface_after_hotplug(self):
        with patch.object(network, 'detect_eth_iface', side_effect=['usb0', None]), \
             patch.object(network.Path, 'exists', return_value=False):
            self.assertEqual(network.get_ethernet_status()['interface'], 'usb0')
            self.assertIsNone(network.get_ethernet_status()['interface'])


class TestRadioCompatibility(unittest.TestCase):
    def test_iw_decimal_frequencies_are_recognized_without_losing_restrictions(self):
        channels = profile.parse_channels(' * 2412.0 MHz [1] (22.0 dBm)\n * 5180.0 MHz [36] (no IR)\n * 5935.5 MHz [2] (-1.0 dBm)')
        self.assertEqual([c.band for c in channels], ['bg', 'a', '6GHz'])
        self.assertEqual(channels[0].frequency, 2412)
        self.assertFalse(channels[1].usable)
        self.assertEqual(channels[2].frequency, 5935.5)

    def test_widths_are_scoped_to_the_band_and_negative_caps_do_not_enable_them(self):
        raw = '''Band 1:
    HT20
    Frequencies:
        * 2412 MHz [1] (20.0 dBm)
Band 2:
    HT20/HT40
    VHT Capabilities:
        Supported Channel Width: neither 160 MHz nor 80+80 MHz
    Frequencies:
        * 5180 MHz [36] (23.0 dBm) (no 160MHz)
Band 4:
    HE160/6GHz
    EHT320/6GHz
    Frequencies:
        * 5955 MHz [1] (17.0 dBm)
'''
        bands = profile.radio_widths_by_band(raw)
        self.assertEqual(bands['bg'], {'20'})
        self.assertEqual(bands['a'], {'20', '40', '80'})
        self.assertIn('320', bands['6GHz'])
        self.assertNotIn('160', bands['a'])

    def test_ht40_blocked_in_both_directions_is_rejected(self):
        channel = profile.Channel(1, 2412, 20, '(no HT40+) (no HT40-)')
        self.assertFalse(profile.channel_allows_width(channel, '40'))
        self.assertTrue(profile.channel_allows_width(channel, '20'))

    def test_negative_dbm_limits_and_selected_band_validation(self):
        channels = profile.parse_channels('* 2412 MHz [1] (-1.0 dBm)')
        self.assertEqual(channels[0].max_dbm, -1.0)
        caps = profile.Capabilities('wifi', 'phy1', 'driver', channels)
        settings = {'802-11-wireless.ssid': 'Test', '802-11-wireless-security.psk': 'TestPassword'}
        profile.validate_settings(settings, caps, tx_power=-5)
        with self.assertRaises(ThinkISPError):
            profile.validate_settings(settings, caps, tx_power=0)

    def test_6ghz_requires_sae_and_cannot_disable_management_frame_protection(self):
        caps = profile.Capabilities('wifi', 'phy1', 'driver', [profile.Channel(5, 5975, 20)], security=['wpa-psk', 'sae'])
        settings = {'802-11-wireless.ssid': 'Test', '802-11-wireless-security.psk': 'TestPassword', '802-11-wireless.band': '6GHz'}
        with self.assertRaisesRegex(ThinkISPError, 'WPA3'):
            profile.validate_settings(settings, caps)
        settings['802-11-wireless-security.key-mgmt'] = 'sae'
        profile.validate_settings(settings, caps)
        settings['802-11-wireless-security.pmf'] = '1'
        with self.assertRaisesRegex(ThinkISPError, 'PMF'):
            profile.validate_settings(settings, caps)

    def test_width_supported_on_another_band_is_not_accepted(self):
        caps = profile.Capabilities('wifi', 'phy1', 'driver', [profile.Channel(1, 2412, 20)],
                                    widths=['auto', '20', '40'], widths_by_band={'bg': ['auto', '20']})
        settings = {'802-11-wireless.ssid': 'Test', '802-11-wireless-security.psk': 'TestPassword',
                    '802-11-wireless.band': 'bg', '802-11-wireless.channel': '1', '802-11-wireless.channel-width': '40'}
        with self.assertRaisesRegex(ThinkISPError, 'otra banda'):
            profile.validate_settings(settings, caps)


class TestCompatibilityDoctor(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(doctor.shutil, 'which', side_effect=lambda name: '/test/bin/' + name))
        self.stack.enter_context(patch.object(doctor.config, 'WLAN_IFACE', 'wifi-test'))
        self.stack.enter_context(patch.object(doctor.config, 'detect_eth_iface', return_value='usb0'))
        self.stack.enter_context(patch.object(doctor, 'load_state', return_value={}))
        self.offline = self.stack.enter_context(patch.object(doctor, '_offline_template', return_value='mode=ap\nmethod=shared'))
        self.stack.enter_context(patch.object(doctor, 'probe_property', side_effect=lambda template, key, value: 'channel-width' not in key))
        caps = profile.Capabilities('wifi-test', 'phy7', 'driver-fixture', [profile.Channel(1, 2412, 20)])
        self.inspect = self.stack.enter_context(patch.object(doctor, 'inspect_capabilities', return_value=caps))
        self.run = self.stack.enter_context(patch.object(doctor, 'run_cmd', side_effect=self.query))

    @staticmethod
    def query(args, **kwargs):
        if args[0] == 'nft':
            return '{"nftables": []}'
        if '--version' in args:
            return 'nmcli tool, version 1.42.0'
        if 'WIFI-HW,WIFI' in args:
            return 'enabled:enabled'
        if 'permissions' in args:
            return 'org.freedesktop.NetworkManager.network-control:yes\norg.freedesktop.NetworkManager.settings.modify.system:auth'
        return 'connected'

    def test_optional_properties_are_detected_and_hardware_activation_is_not_claimed(self):
        report = doctor.collect_report()
        self.assertTrue(report['core_checks_passed'])
        self.assertFalse(report['nmcli_features']['802-11-wireless.channel-width'])
        self.assertFalse(report['wifi']['activation_tested'])
        self.assertEqual(report['wifi']['driver'], 'driver-fixture')
        self.assertIn('networkmanager_permissions', [c['id'] for c in report['checks'] if c['status'] == 'warning'])
        for invocation in self.run.call_args_list:
            args = invocation.args[0]
            self.assertFalse(set(args) & {'modify', 'add', 'up', 'down', 'pkexec', '--ask'})
        self.assertNotIn('802-11-wireless-security.psk', json.dumps(report))

    def test_old_networkmanager_rejected_before_any_activation(self):
        self.offline.side_effect = CommandError(['nmcli'], "unknown option '--offline'")
        report = doctor.collect_report()
        self.assertFalse(report['core_checks_passed'])
        self.assertIn('nmcli_offline', report['failed_required_checks'])
        self.inspect.assert_not_called()

    def test_no_nft_is_optional_and_does_not_disable_basic_hotspot_configuration(self):
        with patch.object(doctor.shutil, 'which', side_effect=lambda name: None if name == 'nft' else '/test/' + name):
            report = doctor.collect_report()
        self.assertTrue(report['core_checks_passed'])
        self.assertEqual(next(c['status'] for c in report['checks'] if c['id'] == 'nft'), 'warning')

    def test_permission_denied_is_not_reported_as_a_missing_device(self):
        def denied(args, **kwargs):
            if kwargs.get('check'):
                raise CommandError(args, 'Could not connect: Permission denied')
            return None
        self.run.side_effect = denied
        report = doctor.collect_report()
        check = next(c for c in report['checks'] if c['id'] == 'networkmanager_service')
        self.assertIn('Permission denied', check['detail'])
        self.assertEqual(report['wifi']['interface'], 'wifi-test')
        self.assertIn('wifi_ap', report['failed_required_checks'])

    def test_cli_json_report_remains_valid_when_required_checks_fail(self):
        from thinkisp.ui import cli
        with patch.object(doctor, 'collect_report', return_value={'core_checks_passed': False}), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            with self.assertRaises(ThinkISPError):
                cli.cmd_doctor(main.build_parser().parse_args(['doctor', '--json']))
        self.assertFalse(json.loads(output.getvalue())['core_checks_passed'])


class TestEntryCompatibility(unittest.TestCase):
    def test_utf8_command_output_is_decoded_even_with_an_ascii_parent_locale(self):
        source = ("from thinkisp.core.network import run_cmd; import sys; "
                  "value=run_cmd([sys.executable,'-c',\"import sys; sys.stdout.buffer.write(bytes.fromhex('706f7274c3a174696c'))\"],check=True); "
                  "assert value == 'port\\u00e1til'")
        env = {**os.environ, 'LC_ALL': 'C', 'PYTHONUTF8': '0', 'PYTHONCOERCECLOCALE': '0',
               'THINKISP_WLAN': 'fixture-wifi', 'THINKISP_ETH': 'fixture-upstream'}
        result = subprocess.run([sys.executable, '-c', source], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_does_not_probe_networkmanager_or_load_curses(self):
        source = "import sys; import main; assert 'thinkisp.config' not in sys.modules; assert 'curses' not in sys.modules; main.build_parser().parse_args(['--help'])"
        with tempfile.TemporaryDirectory() as directory:
            # No tools on PATH, no TTY or daemon required.
            result = subprocess.run([sys.executable, '-c', source], cwd=ROOT,
                                    env={**os.environ, 'PATH': directory}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn('doctor', result.stdout)
        self.assertNotIn('Traceback', result.stderr)

    def test_unsupported_os_has_an_actionable_message(self):
        with patch.object(main.sys, 'platform', 'darwin'), patch('sys.stderr', new_callable=io.StringIO) as error:
            self.assertEqual(main.main(), 1)
        self.assertIn('Linux', error.getvalue())

    def test_missing_or_incapable_terminal_is_an_operational_error(self):
        with patch.object(tui.sys.stdin, 'isatty', return_value=False), patch.object(tui, 'ensure_setup') as setup:
            with self.assertRaisesRegex(ThinkISPError, 'terminal interactiva'):
                tui.launch_tui()
        setup.assert_not_called()
        with patch.object(tui.sys.stdin, 'isatty', return_value=True), \
             patch.object(tui.sys.stdout, 'isatty', return_value=True), patch.dict(os.environ, {'TERM': 'dumb'}):
            with self.assertRaisesRegex(ThinkISPError, 'TERM'):
                tui.launch_tui()
        with patch.object(tui.sys.stdin, 'isatty', return_value=True), \
             patch.object(tui.sys.stdout, 'isatty', return_value=True), patch.dict(os.environ, {'TERM': 'xterm'}), \
             patch.object(tui.locale, 'nl_langinfo', return_value='ANSI_X3.4-1968'):
            with self.assertRaisesRegex(ThinkISPError, 'UTF-8'):
                tui.launch_tui()


class TestInstallerCompatibility(unittest.TestCase):
    def test_installer_quotes_paths_and_installs_only_in_configured_user_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkout = root / 'project & | " quotes $(false)'
            checkout.mkdir()
            for name in ('main.py', 'install.sh'):
                shutil.copy2(ROOT / name, checkout / name)
            for name in ('thinkisp', 'extras'):
                shutil.copytree(ROOT / name, checkout / name, ignore=shutil.ignore_patterns('__pycache__'))
            binaries = root / 'tools'
            binaries.mkdir()
            (binaries / 'python3').symlink_to(sys.executable)
            log = root / 'nm-calls.jsonl'
            fake = binaries / 'nmcli'
            fake.write_text(f'''#!{sys.executable}
import json, sys
from pathlib import Path
with Path({str(log)!r}).open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if '--offline' not in sys.argv:
    sys.exit(9)
print('mode=ap\\nmethod=shared')
''')
            fake.chmod(0o755)
            for name in ('iw', 'ip'):
                target = binaries / name
                target.write_text('#!/bin/sh\nexit 0\n')
                target.chmod(0o755)
            target_bin = root / 'bin & " $percent%'
            env = {**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
                   'THINKISP_BIN_DIR': str(target_bin), 'XDG_DATA_HOME': str(root / 'data'),
                   'XDG_CONFIG_HOME': str(root / 'config'), 'THINKISP_ZSH_DIR': str(root / 'zsh')}
            result = subprocess.run(['bash', str(checkout / 'install.sh')], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in ('thinkisp', 'isp'):
                help_result = subprocess.run([str(target_bin / name), '--help'], env=env, capture_output=True, text=True)
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertIn('doctor', help_result.stdout)
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertTrue(all('--offline' in call for call in calls))
            desktop = root / 'data/applications/thinkisp.desktop'
            self.assertTrue(desktop.exists())
            self.assertTrue((root / 'data/bash-completion/completions/isp').exists())
            self.assertTrue((root / 'config/fish/completions/isp.fish').exists())
            if shutil.which('desktop-file-validate'):
                valid = subprocess.run(['desktop-file-validate', str(desktop)], capture_output=True, text=True)
                self.assertEqual(valid.returncode, 0, valid.stderr + valid.stdout)


if __name__ == '__main__':
    unittest.main()
