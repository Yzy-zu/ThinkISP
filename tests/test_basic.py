#!/usr/bin/env python3
"""
Pruebas básicas unitarias para ThinkISP.
"""

import unittest
from thinkisp import config
from thinkisp.core import dns, network, qos, watcher, wifi


class TestThinkISPBasic(unittest.TestCase):
    def test_config_constants(self):
        """Verifica que las constantes críticas de configuración estén definidas."""
        self.assertEqual(config.HOTSPOT_CONN, "sputnik-hotspot")
        self.assertEqual(config.DEFAULT_SSID, "sputnik")
        self.assertTrue(hasattr(config, "STATE_DIR"))
        self.assertTrue(hasattr(config, "DNS_PROVIDERS"))

    def test_dns_providers(self):
        """Verifica que los proveedores de DNS estén correctamente configurados."""
        required_keys = {"name", "ip_test", "servers", "desc"}
        for key in ["cloudflare", "google", "quad9", "adguard"]:
            self.assertIn(key, config.DNS_PROVIDERS)
            provider = config.DNS_PROVIDERS[key]
            for field in required_keys:
                self.assertIn(field, provider)
                self.assertTrue(len(provider[field]) > 0)


if __name__ == "__main__":
    unittest.main()
