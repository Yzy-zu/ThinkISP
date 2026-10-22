"""Pruebas unitarias para el firewall nativo del portal cautivo."""

import copy
import json
import unittest
from unittest.mock import patch

from thinkisp.core.network import CommandError, ThinkISPError
from thinkisp.core.portal_firewall import PortalFirewall, portal_table_name


OWNER = "11111111-1111-1111-1111-111111111111"
OTHER_OWNER = "22222222-2222-2222-2222-222222222222"
INSTANCE = "33333333-3333-3333-3333-333333333333"
OTHER_INSTANCE = "44444444-4444-4444-4444-444444444444"
IFACE = "ap0"
SUBNET = "192.168.50.0/24"
GATEWAY = "192.168.50.1"
PORT = 8080
MAC_1 = "02:00:00:00:00:01"
MAC_2 = "02:00:00:00:00:02"
IP_1 = "192.168.50.10"
IP_2 = "192.168.50.20"


class FakeNftHost:
    """Simulador de host nftables para validar transacciones JSON atómicas."""

    def __init__(self):
        self.tables = {}
        self.flowtables = []
        self.calls = []
        self.fail_batch = False
        self.permission_denied = False

    def run(self, argv, input_text=None, check=True, timeout=120):
        self.calls.append((list(argv), input_text))

        if self.permission_denied:
            raise CommandError(argv, "Error: Could not process rule: Operation not permitted", returncode=1)

        args = argv[1:] if argv and argv[0].endswith("nft") else argv

        if args == ["-j", "list", "tables"]:
            entries = []
            for (family, name) in self.tables.keys():
                entries.append({"table": {"family": family, "name": name}})
            return json.dumps({"nftables": entries})

        if args == ["-j", "list", "ruleset"]:
            entries = []
            for ft in self.flowtables:
                entries.append({"flowtable": ft})
            for objs in self.tables.values():
                entries.extend(objs)
            return json.dumps({"nftables": entries})

        if len(args) == 5 and args[:4] == ["-j", "list", "table", "inet"]:
            key = ("inet", args[4])
            if key not in self.tables:
                raise CommandError(argv, f"Error: No such file or directory; Table '{args[4]}' does not exist", returncode=1)
            return json.dumps({"nftables": self.tables[key]})

        if args == ["-j", "-f", "-"]:
            if self.fail_batch:
                raise CommandError(argv, "Error: syntax error in batch transaction", returncode=1)
            commands = json.loads(input_text)["nftables"]
            temp_tables = copy.deepcopy(self.tables)

            for cmd in commands:
                if "delete" in cmd:
                    del_obj = cmd["delete"]["table"]
                    key = (del_obj["family"], del_obj["name"])
                    if key in temp_tables:
                        del temp_tables[key]
                    else:
                        raise CommandError(argv, f"Error: table {key} not found for deletion", returncode=1)
                elif "flush" in cmd:
                    flush_obj = cmd["flush"].get("set")
                    if flush_obj:
                        key = (flush_obj["family"], flush_obj["table"])
                        if key in temp_tables:
                            for item in temp_tables[key]:
                                if "set" in item and item["set"]["name"] == flush_obj["name"]:
                                    item["set"]["elem"] = []
                elif "add" in cmd or "create" in cmd:
                    add_obj = cmd.get("add", cmd.get("create"))
                    if "table" in add_obj:
                        t = add_obj["table"]
                        key = (t["family"], t["name"])
                        if key in temp_tables:
                            if 'create' in cmd:
                                raise CommandError(argv, 'File exists', returncode=1)
                        else:
                            temp_tables[key] = [{"table": t}]
                    elif "set" in add_obj:
                        s = add_obj["set"]
                        key = (s["family"], s["table"])
                        temp_tables[key].append({"set": s})
                    elif "chain" in add_obj:
                        c = add_obj["chain"]
                        key = (c["family"], c["table"])
                        temp_tables[key].append({"chain": c})
                    elif "rule" in add_obj:
                        r = add_obj["rule"]
                        key = (r["family"], r["table"])
                        temp_tables[key].append({"rule": r})
                    elif "element" in add_obj:
                        el = add_obj["element"]
                        key = (el["family"], el["table"])
                        for item in temp_tables[key]:
                            if "set" in item and item["set"]["name"] == el["name"]:
                                item["set"].setdefault("elem", []).extend(el.get("elem", []))

            self.tables = temp_tables
            return ""

        raise CommandError(argv, f"Comando no simulado: {argv}", returncode=1)


class TestValidation(unittest.TestCase):
    def test_owner_uuid_validation(self):
        fake = FakeNftHost()
        # Válido normal
        fw = PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, PORT, INSTANCE, runner=fake.run)
        self.assertEqual(fw.owner, OWNER)

        # Válido mayúsculas
        fw_upper = PortalFirewall(OWNER.upper(), IFACE, SUBNET, GATEWAY, PORT, INSTANCE, runner=fake.run)
        self.assertEqual(fw_upper.owner, OWNER)

        # Inválidos
        for bad_owner in ["not-a-uuid", "1234", "", None, 12345]:
            with self.subTest(bad_owner=bad_owner):
                with self.assertRaises(ThinkISPError):
                    PortalFirewall(bad_owner, IFACE, SUBNET, GATEWAY, PORT, INSTANCE, runner=fake.run)

    def test_interface_name_validation(self):
        fake = FakeNftHost()
        # Válidos
        for iface in ["wlan0", "ap-0", "ap_wifi", "eth1.100"]:
            fw = PortalFirewall(OWNER, iface, SUBNET, GATEWAY, PORT, INSTANCE, runner=fake.run)
            self.assertEqual(fw.interface, iface)

        # Inválidos
        for bad_iface in ["", None, "interface_too_long_16", "wlan 0", "ap/0", "ap:0", "ap\x00", "ap\n0"]:
            with self.subTest(bad_iface=bad_iface):
                with self.assertRaises(ThinkISPError):
                    PortalFirewall(OWNER, bad_iface, SUBNET, GATEWAY, PORT, INSTANCE, runner=fake.run)

    def test_subnet_and_gateway_validation(self):
        fake = FakeNftHost()
        # Válidos
        fw = PortalFirewall(OWNER, IFACE, "10.0.0.0/8", "10.0.0.1", PORT, INSTANCE, runner=fake.run)
        self.assertEqual(str(fw.gateway), "10.0.0.1")

        # Gateway fuera de la subred
        with self.assertRaises(ThinkISPError):
            PortalFirewall(OWNER, IFACE, "192.168.1.0/24", "192.168.2.1", PORT, INSTANCE, runner=fake.run)

        # Gateway igual a network address
        with self.assertRaises(ThinkISPError):
            PortalFirewall(OWNER, IFACE, "192.168.1.0/24", "192.168.1.0", PORT, INSTANCE, runner=fake.run)

        # Gateway igual a broadcast address
        with self.assertRaises(ThinkISPError):
            PortalFirewall(OWNER, IFACE, "192.168.1.0/24", "192.168.1.255", PORT, INSTANCE, runner=fake.run)

        # Subred demasiado pequeña (/31 y /32)
        with self.assertRaises(ThinkISPError):
            PortalFirewall(OWNER, IFACE, "192.168.1.0/31", "192.168.1.1", PORT, INSTANCE, runner=fake.run)
        with self.assertRaises(ThinkISPError):
            PortalFirewall(OWNER, IFACE, "192.168.1.1/32", "192.168.1.1", PORT, INSTANCE, runner=fake.run)

        # Subred no estricta con bits de host
        with self.assertRaises(ThinkISPError):
            PortalFirewall(OWNER, IFACE, "192.168.1.5/24", "192.168.1.1", PORT, INSTANCE, runner=fake.run)

        # IPv6 o strings inválidos
        with self.assertRaises(ThinkISPError):
            PortalFirewall(OWNER, IFACE, "2001:db8::/64", "2001:db8::1", PORT, INSTANCE, runner=fake.run)

    def test_port_validation(self):
        fake = FakeNftHost()
        # Válidos
        for port in [1024, 8080, 65535]:
            fw = PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, port, INSTANCE, runner=fake.run)
            self.assertEqual(fw.port, port)

        # Inválidos
        for bad_port in [80, 443, 1023, 65536, -1, True, False, "8080", None]:
            with self.subTest(bad_port=bad_port):
                with self.assertRaises(ThinkISPError):
                    PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, bad_port, INSTANCE, runner=fake.run)

    def test_instance_uuid_validation(self):
        fake = FakeNftHost()
        for bad_inst in ["not-a-uuid", "", None, 123]:
            with self.subTest(bad_inst=bad_inst):
                with self.assertRaises(ThinkISPError):
                    PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, PORT, bad_inst, runner=fake.run)

    def test_portal_table_name_deterministic_and_uid_independent(self):
        name1 = portal_table_name(OWNER)
        name2 = portal_table_name(OWNER)
        self.assertEqual(name1, name2)
        self.assertTrue(name1.startswith("thinkisp_portal_"))
        self.assertNotEqual(portal_table_name(OWNER), portal_table_name(OTHER_OWNER))

        # Mayúsculas normalizan al mismo nombre
        self.assertEqual(portal_table_name(OWNER.upper()), name1)

    def test_default_runner_raises_if_nft_missing(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(ThinkISPError, "nftables"):
                PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, PORT, INSTANCE)


class TestInstallationAndPreflight(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNftHost()
        self.fw = PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, PORT, INSTANCE, runner=self.fake.run)

    def test_successful_installation_creates_expected_table(self):
        self.fw.install()
        key = ("inet", self.fw.table)
        self.assertIn(key, self.fake.tables)
        summary = self.fw.verify()
        self.assertTrue(summary["verified"])
        self.assertEqual(summary["table"], self.fw.table)
        self.assertEqual(summary["authorized_count"], 0)

    def test_install_refuses_existing_table_with_same_name(self):
        # Crear tabla previa
        self.fake.tables[("inet", self.fw.table)] = [{"table": {"family": "inet", "name": self.fw.table}}]
        with self.assertRaisesRegex(ThinkISPError, "ya existe"):
            self.fw.install()

    def test_install_refuses_if_flowtable_offload_involves_ap_interface(self):
        self.fake.flowtables.append({
            "family": "inet",
            "name": "fastpath",
            "dev": [IFACE, "eth0"]
        })
        with self.assertRaisesRegex(ThinkISPError, "offload"):
            self.fw.install()

    def test_install_refuses_offload_that_cannot_be_safely_integrated(self):
        self.fake.flowtables.append({
            "family": "inet",
            "name": "fastpath",
            "dev": ["eth0", "eth1"]
        })
        with self.assertRaisesRegex(ThinkISPError, "offload"):
            self.fw.install()
        self.assertNotIn(("inet", self.fw.table), self.fake.tables)

    def test_install_refuses_if_flow_add_rule_involves_ap(self):
        # Regla que añade flows sin restricción de interfaz
        self.fake.tables[("inet", "router_filter")] = [
            {"table": {"family": "inet", "name": "router_filter"}},
            {"rule": {
                "family": "inet", "table": "router_filter", "chain": "forward",
                "expr": [{"flow": {"op": "add", "flowtable": "ft"}}]
            }}
        ]
        with self.assertRaisesRegex(ThinkISPError, "offload"):
            self.fw.install()

    def test_permission_denied_raises_actionable_error(self):
        self.fake.permission_denied = True
        with self.assertRaisesRegex(ThinkISPError, "permisos de administrador"):
            self.fw.install()

    def test_transaction_failure_raises_thinkisp_error(self):
        self.fake.fail_batch = True
        with self.assertRaises(CommandError):
            self.fw.install()
        self.assertEqual(self.fake.tables, {})


class TestRulesAndInvariants(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNftHost()
        self.fw = PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, PORT, INSTANCE, runner=self.fake.run)
        self.fw.install()
        self.objects = self.fake.tables[("inet", self.fw.table)]

    def test_table_marker_matches_exact_comment(self):
        table_obj = next(item["table"] for item in self.objects if "table" in item)
        self.assertEqual(table_obj["comment"], f"thinkISP portal:{OWNER}:{INSTANCE}")

    def test_prerouting_nat_priority_and_redirect(self):
        prerouting = next(item["chain"] for item in self.objects if item.get("chain", {}).get("name") == "prerouting")
        self.assertEqual(prerouting["type"], "nat")
        self.assertEqual(prerouting["hook"], "prerouting")
        self.assertEqual(prerouting["prio"], -101)

        rule = next(item["rule"] for item in self.objects if item.get("rule", {}).get("chain") == "prerouting")
        expr = rule["expr"]
        # Debe redireccionar a self.port
        self.assertTrue(any(s.get("redirect", {}).get("port") == PORT for s in expr))
        # Debe cotejar tupla no autorizada
        self.assertTrue(any(s.get("match", {}).get("op") == "!="
                            and s.get("match", {}).get("right") == "@authorized_tuples" for s in expr))

    def test_input_chain_priority_policy_and_rules(self):
        input_chain = next(item["chain"] for item in self.objects if item.get("chain", {}).get("name") == "input")
        self.assertEqual(input_chain["type"], "filter")
        self.assertEqual(input_chain["hook"], "input")
        self.assertEqual(input_chain["prio"], -150)
        self.assertEqual(input_chain["policy"], "accept")

        rules = [item["rule"] for item in self.objects if item.get("rule", {}).get("chain") == "input"]
        self.assertEqual(len(rules), 5)
        # La última regla debe ser drop en la interfaz AP
        last_expr = rules[-1]["expr"]
        self.assertTrue(any(s.get("match", {}).get("left", {}).get("meta", {}).get("key") == "iifname"
                            and s.get("match", {}).get("right") == IFACE for s in last_expr))
        self.assertIn({"drop": None}, last_expr)

    def test_forward_chain_priority_policy_and_rules(self):
        fwd_chain = next(item["chain"] for item in self.objects if item.get("chain", {}).get("name") == "forward")
        self.assertEqual(fwd_chain["type"], "filter")
        self.assertEqual(fwd_chain["hook"], "forward")
        self.assertEqual(fwd_chain["prio"], -150)
        self.assertEqual(fwd_chain["policy"], "accept")

        rules = [item["rule"] for item in self.objects if item.get("rule", {}).get("chain") == "forward"]
        self.assertEqual(len(rules), 4)

        # Regla 1: AP -> Internet autorizados
        r1_expr = rules[0]["expr"]
        self.assertTrue(any(s.get("match", {}).get("right") == "@authorized_tuples" for s in r1_expr))
        self.assertIn({"accept": None}, r1_expr)

        # Regla 2: AP -> Internet drop
        r2_expr = rules[1]["expr"]
        self.assertIn({"drop": None}, r2_expr)

        # Regla 3: Internet -> AP autorizados + conntrack
        r3_expr = rules[2]["expr"]
        self.assertTrue(any(s.get("match", {}).get("right") == "@authorized_ips" for s in r3_expr))
        self.assertTrue(any(s.get("match", {}).get("left", {}).get("ct", {}).get("key") == "state" for s in r3_expr))
        self.assertIn({"accept": None}, r3_expr)

        # Regla 4: Internet -> AP drop
        r4_expr = rules[3]["expr"]
        self.assertIn({"drop": None}, r4_expr)

    def test_sets_have_timeout_flag_and_lease(self):
        sets = {item["set"]["name"]: item["set"] for item in self.objects if "set" in item}
        self.assertIn("authorized_tuples", sets)
        self.assertIn("authorized_ips", sets)
        self.assertIn("timeout", sets["authorized_tuples"]["flags"])
        self.assertIn("timeout", sets["authorized_ips"]["flags"])
        self.assertEqual(sets["authorized_tuples"]["timeout"], 15)
        self.assertEqual(sets["authorized_ips"]["timeout"], 15)


class TestAuthorizationManagement(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNftHost()
        self.fw = PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, PORT, INSTANCE, runner=self.fake.run)
        self.fw.install()

    def test_authorize_clients_updates_sets_atomically(self):
        bindings = [
            {"ip": IP_1, "mac": MAC_1},
            {"ip": IP_2, "mac": MAC_2},
        ]
        self.fw.set_authorized(bindings)
        summary = self.fw.verify()
        self.assertEqual(summary["authorized_count"], 2)

        # Comprobar elementos añadidos
        objects = self.fake.tables[("inet", self.fw.table)]
        tuples_set = next(item["set"] for item in objects if item.get("set", {}).get("name") == "authorized_tuples")
        ips_set = next(item["set"] for item in objects if item.get("set", {}).get("name") == "authorized_ips")
        self.assertEqual(len(tuples_set["elem"]), 2)
        self.assertEqual(len(ips_set["elem"]), 2)

        # Comprobar que cada elemento tiene timeout 15
        for el in tuples_set["elem"]:
            self.assertEqual(el["elem"]["timeout"], 15)
        for el in ips_set["elem"]:
            self.assertEqual(el["elem"]["timeout"], 15)

    def test_mac_normalization(self):
        raw_mac = " 02:00:00:00:00:0A "
        self.fw.set_authorized([{"ip": IP_1, "mac": raw_mac}])
        objects = self.fake.tables[("inet", self.fw.table)]
        tuples_set = next(item["set"] for item in objects if item.get("set", {}).get("name") == "authorized_tuples")
        added_mac = tuples_set["elem"][0]["elem"]["val"]["concat"][0]
        self.assertEqual(added_mac, "02:00:00:00:00:0a")

    def test_invalid_macs_rejected(self):
        # Multicast, broadcast, ceros, malformadas
        for bad_mac in ["01:00:5e:00:00:01", "ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00", "invalid"]:
            with self.subTest(bad_mac=bad_mac):
                with self.assertRaises(ThinkISPError):
                    self.fw.set_authorized([{"ip": IP_1, "mac": bad_mac}])

    def test_invalid_ips_rejected(self):
        # Fuera de subred, network, broadcast, gateway, IPv6
        for bad_ip in ["192.168.1.10", "192.168.50.0", "192.168.50.255", GATEWAY, "2001:db8::1", "not-an-ip"]:
            with self.subTest(bad_ip=bad_ip):
                with self.assertRaises(ThinkISPError):
                    self.fw.set_authorized([{"ip": bad_ip, "mac": MAC_1}])

    def test_contradictory_mappings_rejected(self):
        # Misma IP con distinta MAC
        bad1 = [{"ip": IP_1, "mac": MAC_1}, {"ip": IP_1, "mac": MAC_2}]
        with self.assertRaisesRegex(ThinkISPError, "Mapeo contradictorio"):
            self.fw.set_authorized(bad1)

        # Misma MAC con distinta IP
        bad2 = [{"ip": IP_1, "mac": MAC_1}, {"ip": IP_2, "mac": MAC_1}]
        with self.assertRaisesRegex(ThinkISPError, "Mapeo contradictorio"):
            self.fw.set_authorized(bad2)

    def test_duplicate_identical_mappings_deduplicated(self):
        same = [{"ip": IP_1, "mac": MAC_1}, {"ip": IP_1, "mac": MAC_1}]
        self.fw.set_authorized(same)
        summary = self.fw.verify()
        self.assertEqual(summary["authorized_count"], 1)

    def test_empty_set_revokes_all(self):
        self.fw.set_authorized([{"ip": IP_1, "mac": MAC_1}])
        self.assertEqual(self.fw.verify()["authorized_count"], 1)
        self.fw.set_authorized([])
        self.assertEqual(self.fw.verify()["authorized_count"], 0)

    def test_base_chains_and_table_not_recreated_on_set_update(self):
        call_count_before = len(self.fake.calls)
        self.fw.set_authorized([{"ip": IP_1, "mac": MAC_1}])
        # El batch de set_authorized solo contiene flush y add element, nunca add table o add chain
        last_batch = next(payload for _, payload in reversed(self.fake.calls) if payload)
        cmds = json.loads(last_batch)["nftables"]
        for cmd in cmds:
            if "add" in cmd:
                self.assertNotIn("table", cmd["add"])
                self.assertNotIn("chain", cmd["add"])


class TestVerificationAndRemoval(unittest.TestCase):
    def setUp(self):
        self.fake = FakeNftHost()
        self.fw = PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, PORT, INSTANCE, runner=self.fake.run)
        self.fw.install()

    def test_verify_rejects_missing_table(self):
        del self.fake.tables[("inet", self.fw.table)]
        with self.assertRaisesRegex(ThinkISPError, "No se encontró"):
            self.fw.verify()

    def test_verify_rejects_marker_mismatch(self):
        objects = self.fake.tables[("inet", self.fw.table)]
        table_obj = next(item["table"] for item in objects if "table" in item)
        table_obj["comment"] = "thinkISP portal:other_owner:other_instance"
        with self.assertRaisesRegex(ThinkISPError, "no coincide"):
            self.fw.verify()

    def test_verify_rejects_mutated_chain_properties(self):
        objects = self.fake.tables[("inet", self.fw.table)]
        prerouting = next(item["chain"] for item in objects if item.get("chain", {}).get("name") == "prerouting")
        prerouting["prio"] = -100  # Mutado
        with self.assertRaisesRegex(ThinkISPError, "no coinciden"):
            self.fw.verify()

    def test_verify_rejects_missing_sets(self):
        objects = self.fake.tables[("inet", self.fw.table)]
        self.fake.tables[("inet", self.fw.table)] = [item for item in objects if item.get("set", {}).get("name") != "authorized_ips"]
        with self.assertRaisesRegex(ThinkISPError, "no coinciden"):
            self.fw.verify()

    def test_verify_rejects_tampered_rules(self):
        objects = self.fake.tables[("inet", self.fw.table)]
        # Alterar puerto de redirección
        for item in objects:
            if item.get("rule", {}).get("chain") == "prerouting":
                for s in item["rule"]["expr"]:
                    if "redirect" in s:
                        s["redirect"]["port"] = 9999
        with self.assertRaisesRegex(ThinkISPError, "no coincide"):
            self.fw.verify()

    def test_remove_deletes_own_table_atomically(self):
        # Añadir tabla externa para confirmar que no se toca
        self.fake.tables[("inet", "external_fixture")] = [{"table": {"family": "inet", "name": "external_fixture"}}]
        self.fw.remove()
        self.assertNotIn(("inet", self.fw.table), self.fake.tables)
        self.assertIn(("inet", "external_fixture"), self.fake.tables)

    def test_remove_is_idempotent_when_table_already_absent(self):
        self.fw.remove()
        # Segunda llamada cuando ya no existe
        self.fw.remove()
        self.assertNotIn(("inet", self.fw.table), self.fake.tables)

    def test_remove_refuses_marker_mismatch(self):
        objects = self.fake.tables[("inet", self.fw.table)]
        table_obj = next(item["table"] for item in objects if "table" in item)
        table_obj["comment"] = "alien_portal"
        with self.assertRaisesRegex(ThinkISPError, "no coincide"):
            self.fw.remove()
        self.assertIn(("inet", self.fw.table), self.fake.tables)

    def test_remove_never_flushes_global_ruleset_or_other_tables(self):
        self.fake.tables[("inet", "external")] = [{"table": {"family": "inet", "name": "external"}}]
        self.fake.tables[("netdev", "thinkisp_block")] = [{"table": {"family": "netdev", "name": "thinkisp_block"}}]
        self.fw.remove()
        self.assertIn(("inet", "external"), self.fake.tables)
        self.assertIn(("netdev", "thinkisp_block"), self.fake.tables)


if __name__ == "__main__":
    unittest.main()

class TestPortalRuleIntegrity(unittest.TestCase):
    def make(self):
        host = FakeNftHost()
        fw = PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, PORT, INSTANCE, runner=host.run)
        fw.install()
        return fw, host

    def test_each_predicate_and_order_is_verified(self):
        fw, host = self.make()
        key = ('inet', fw.table)
        original = copy.deepcopy(host.tables[key])
        rules = [i for i, obj in enumerate(original) if 'rule' in obj]
        for i in rules:
            expressions = original[i]['rule']['expr']
            for j in range(len(expressions)):
                changed = copy.deepcopy(original)
                changed[i]['rule']['expr'].pop(j)
                host.tables[key] = changed
                with self.subTest(rule=i, predicate=j), self.assertRaises(ThinkISPError):
                    fw.verify()
        host.tables[key] = copy.deepcopy(original)
        a, b = rules[-2:]
        host.tables[key][a], host.tables[key][b] = host.tables[key][b], host.tables[key][a]
        with self.assertRaises(ThinkISPError):
            fw.verify()

    def test_lease_and_membership_mismatch_do_not_verify(self):
        for mutation in ('permanent', 'extra_ip', 'wrong_subnet'):
            fw, host = self.make()
            fw.set_authorized([{'ip': IP_1, 'mac': MAC_1}])
            objects = host.tables[('inet', fw.table)]
            sets = {o['set']['name']: o['set'] for o in objects if 'set' in o}
            if mutation == 'permanent':
                sets['authorized_ips']['timeout'] = 0
            elif mutation == 'extra_ip':
                sets['authorized_ips']['elem'].append({'elem': {'val': IP_2, 'timeout': 15}})
            else:
                sets['authorized_tuples']['elem'][0]['elem']['val']['concat'][1] = '203.0.113.1'
            with self.subTest(mutation=mutation), self.assertRaises(ThinkISPError):
                fw.verify()

    def test_empty_or_malformed_inventory_never_means_absent(self):
        for output in ('', '{}', 'null', '[]', '{"nftables":[null]}'):
            fw, host = self.make()
            fw._runner = lambda *args, **kwargs: output
            with self.subTest(output=output), self.assertRaises(ThinkISPError):
                fw.remove()

    def test_exclusive_create_rejects_preflight_race(self):
        host = FakeNftHost()
        fw = PortalFirewall(OWNER, IFACE, SUBNET, GATEWAY, PORT, INSTANCE, runner=host.run)
        def raced(args, **kwargs):
            if kwargs.get('input_text'):
                host.tables[('inet', fw.table)] = [{'table': {'family':'inet', 'name':fw.table,'comment':'foreign'}}]
            return host.run(args, **kwargs)
        fw._runner = raced
        with self.assertRaises(CommandError):
            fw.install()
        self.assertEqual(host.tables[('inet',fw.table)][0]['table']['comment'], 'foreign')

    def test_acknowledged_but_unapplied_authorization_is_failure(self):
        fw, host = self.make()
        def ignored(args, **kwargs):
            if kwargs.get('input_text'):
                return ''
            return host.run(args, **kwargs)
        fw._runner = ignored
        with self.assertRaises(ThinkISPError):
            fw.set_authorized([{'ip':IP_1,'mac':MAC_1}])
