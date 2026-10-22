"""Detección por equipo: no inventar adaptadores ni reutilizar otro hardware."""
import os
import unittest
from unittest.mock import patch

from thinkisp import config


class TestDynamicConfiguration(unittest.TestCase):
    def test_no_wifi_returns_none_instead_of_an_invented_name(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(config, '_probe', return_value=''), \
             patch('pathlib.Path.exists', return_value=False):
            self.assertIsNone(config.detect_wlan_iface())

    def test_wifi_detection_prefers_an_adapter_with_ap_support(self):
        def probe(args):
            if 'DEVICE,TYPE' in args:
                return 'internal-wifi:wifi\nusb-ap:wifi'
            if 'WIFI-PROPERTIES.AP' in args:
                return 'yes' if args[-1] == 'usb-ap' else 'no'
            return ''
        with patch.dict(os.environ, {}, clear=True), patch.object(config, '_probe', side_effect=probe):
            self.assertEqual(config.detect_wlan_iface(), 'usb-ap')

    def test_existing_profile_keeps_its_adapter(self):
        def probe(args):
            if 'DEVICE,TYPE' in args:
                return 'internal-wifi:wifi\nusb-ap:wifi'
            if 'connection.interface-name' in args:
                return 'internal-wifi'
            return 'yes'
        with patch.dict(os.environ, {}, clear=True), patch.object(config, '_probe', side_effect=probe):
            self.assertEqual(config.detect_wlan_iface(), 'internal-wifi')

    def test_explicit_adapter_selection_is_respected(self):
        with patch.dict(os.environ, {'THINKISP_WLAN': 'chosen-wifi'}):
            self.assertEqual(config.detect_wlan_iface(), 'chosen-wifi')

    def test_no_upstream_returns_none(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(config, '_probe', return_value=''), \
             patch('pathlib.Path.exists', return_value=False):
            self.assertIsNone(config.detect_eth_iface('wifi-test'))

    def test_upstream_is_selected_from_actual_default_route(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(config, '_probe', return_value='default via 192.0.2.1 dev usb-upstream metric 100'):
            self.assertEqual(config.detect_eth_iface('wifi-test'), 'usb-upstream')

    def test_initial_ssid_depends_on_the_computer(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(config.socket, 'gethostname', return_value='computer-one'):
                first = config.default_ssid()
            with patch.object(config.socket, 'gethostname', return_value='computer-two'):
                second = config.default_ssid()
        self.assertNotEqual(first, second)
        self.assertIn('computer-one', first)

    def test_ssid_suggestion_obeys_the_protocol_byte_limit(self):
        with patch.dict(os.environ, {'THINKISP_SSID': 'á' * 40}):
            self.assertLessEqual(len(config.default_ssid().encode('utf-8')), 32)


if __name__ == '__main__':
    unittest.main()
