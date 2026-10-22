"""Topology across independent hosts, radios and input links; no live mutations."""
from contextlib import nullcontext
import copy
import json
import unittest
from unittest.mock import patch
from thinkisp.core import topology as t, deployment as d
from thinkisp.core.network import ThinkISPError

RADIO = '''Wiphy phy9
    Supported interface modes:
         * managed
         * AP
    Band 1:
        Frequencies:
            * 2437 MHz [6] (20.0 dBm)
    valid interface combinations:
         * #{ managed } <= 1, #{ AP, P2P-GO } <= 1,
           total <= 3, #channels <= 1
    Device supports scan flush.
'''
A='414124d1-5a36-446c-a509-aa1f951639bf'
B='b78e3910-f406-464e-89cc-1c0249cfd900'


def inventory(kind='ethernet', same=False, combinations=None):
    radios={'phy9':{'verified':True,'modes':['managed','AP'],'ap_channels':[6],
                    'combinations':t.interface_combinations(RADIO) if combinations is None else combinations}}
    devices=[{'name':'radio-ap','kind':'wifi','managed':True,'phy':'phy9','state':'connected','bus':'other'}]
    upstream='radio-ap' if same else 'entrada'
    if not same:
        devices.append({'name':upstream,'kind':kind,'managed':True,'phy':'phy10' if kind=='wifi' else None,
                        'state':'connected','bus':'usb' if kind=='ethernet' else 'other'})
    return {'devices':devices,'radios':radios,'routes':[{'interface':upstream,'family':4,'metric':100}],
            'engines':{'none':{'installed':True},'opennds':{'installed':False}},
            'dependencies':{'nft':True},'firewall':{'status':'readable','table_count':0},'errors':[]}


def plan(data=None, **kwargs):
    return t.evaluate_plan(data or inventory(), t.NetworkIntent(**kwargs),fallback_ap='radio-ap')


class TestRadioCombinations(unittest.TestCase):
    def test_ap_and_managed_in_separate_groups_allow_same_channel(self):
        self.assertEqual(t.interface_combinations(RADIO),[{'managed_ap':True,'max_interfaces':3,'max_channels':1,'limits':[{'modes':['managed'],'limit':1},{'modes':['AP','P2P-GO'],'limit':1}]}])

    def test_shared_group_with_limit_one_cannot_host_both_modes(self):
        raw=RADIO.replace('#{ managed } <= 1, #{ AP, P2P-GO } <= 1','#{ managed, AP, P2P-GO } <= 1')
        self.assertFalse(t.interface_combinations(raw)[0]['managed_ap'])

    def test_p2p_go_does_not_substitute_for_ap(self):
        self.assertFalse(t.interface_combinations(RADIO.replace('AP, ',''))[0]['managed_ap'])

    def test_total_budget_is_checked(self):
        self.assertFalse(t.interface_combinations(RADIO.replace('total <= 3','total <= 1'))[0]['managed_ap'])

    def test_multiple_alternatives_can_allow_two_channels(self):
        raw=RADIO.replace('    Device supports', '         * #{ managed, AP } <= 2, total <= 2, #channels <= 2\n    Device supports')
        result=plan(inventory(same=True,combinations=t.interface_combinations(raw)))
        self.assertEqual(result['concurrency']['status'],'advertised')
        self.assertFalse(result['concurrency']['same_channel_required'])


class TestTopologyPlans(unittest.TestCase):
    def test_wired_usb_and_separate_wifi_use_routed_topology(self):
        for kind in ('ethernet','wifi','bridge','tun'):
            with self.subTest(kind=kind):
                result=plan(inventory(kind))
                self.assertEqual(result['selected']['mode'],'routed')
                self.assertTrue(result['configuration_valid'])
                self.assertFalse(result['applied'])
                self.assertFalse(result['portal_enabled'])

    def test_one_radio_is_repeater_plan_but_never_runtime_support_claim(self):
        result=plan(inventory(same=True))
        self.assertEqual(result['selected']['mode'],'repeater')
        self.assertEqual(result['concurrency']['status'],'advertised')
        self.assertTrue(result['concurrency']['same_channel_required'])
        self.assertFalse(result['activation_tested'])
        self.assertIn('repeater_activation_unverified',[x['code'] for x in result['issues']])

    def test_different_virtual_interfaces_on_same_phy_need_repeater(self):
        data=inventory(kind='wifi')
        data['devices'][1]['phy']='phy9'
        self.assertEqual(plan(data)['selected']['mode'],'repeater')
        self.assertFalse(plan(data,mode='routed')['configuration_valid'])

    def test_unknown_physical_radios_are_not_assumed_independent(self):
        data=inventory(kind='wifi');data['devices'][1]['phy']=None
        result=plan(data)
        self.assertEqual(result['concurrency']['status'],'unverified')
        self.assertIn('radio_identity_unknown',[item['code'] for item in result['issues']])

    def test_same_interface_is_rejected_in_routed_mode(self):
        self.assertFalse(plan(inventory(same=True),mode='routed')['configuration_valid'])

    def test_supported_modes_without_combinations_are_unverified(self):
        result=plan(inventory(same=True,combinations=[]))
        self.assertEqual(result['concurrency']['status'],'unverified')
        self.assertFalse(result['activation_tested'])

    def test_known_unsupported_concurrency_rejects_plan(self):
        result=plan(inventory(same=True,combinations=[{'managed_ap':False,'max_channels':1}]))
        self.assertFalse(result['configuration_valid'])

    def test_missing_unmanaged_or_wrong_type_ap_is_rejected(self):
        for change in ({'kind':'ethernet'},{'managed':False},{'name':'renamed-device'}):
            data=inventory();data['devices'][0].update(change)
            self.assertFalse(plan(data)['configuration_valid'])

    def test_no_usable_channels_is_rejected(self):
        data=inventory();data['radios']['phy9']['ap_channels']=[]
        self.assertFalse(plan(data)['configuration_valid'])

    def test_explicit_input_is_preserved_without_claiming_internet(self):
        data=inventory();data['routes']=[]
        result=plan(data,upstream_interface='entrada')
        self.assertEqual(result['selected']['upstream_interface'],'entrada')
        self.assertIn('upstream_no_route',[x['code'] for x in result['issues']])
        self.assertFalse(result['internet_verified'])

    def test_default_route_priority_ipv4_then_metric(self):
        data=inventory();data['routes']=[{'interface':'radio-ap','family':6,'metric':1},
                                        {'interface':'radio-ap','family':4,'metric':500},
                                        {'interface':'entrada','family':4,'metric':100}]
        self.assertEqual(plan(data)['selected']['upstream_interface'],'entrada')

    def test_opennds_installed_is_not_integrated_or_enabled(self):
        data=inventory();data['engines']['opennds']={'installed':True,'control_installed':True}
        result=plan(data)
        self.assertEqual(result['selected']['portal_engine'],'opennds')
        self.assertFalse(result['portal_enabled'])
        self.assertIn('portal_runtime_pending',[x['code'] for x in result['issues']])

    def test_existing_firewall_requires_compatibility_check_without_changes(self):
        data=inventory();data['firewall']['table_count']=8
        self.assertIn('firewall_existing',[x['code'] for x in plan(data)['issues']])

    def test_route_parser_filters_dead_blackhole_and_expands_multipath(self):
        routes=[{'type':'blackhole','dev':'bad'}, {'dev':'bad','flags':['linkdown']},
                {'metric':20,'nexthops':[{'dev':'good'},{'dev':'bad','flags':['dead']}]}]
        self.assertEqual(t.parse_routes(json.dumps(routes),4),[{'interface':'good','family':4,'metric':20}])

    def test_invalid_intents_do_not_raise_unhandled_type_errors(self):
        for values in ({'ap_interface':'../bad'},{'ap_interface':'x'*16},{'mode':[]},{'portal_engine':{}}, {'upstream_interface':'a\nb'}):
            with self.subTest(values=values),self.assertRaises(ThinkISPError):
                t.NetworkIntent(**values).validate()


class TestInventory(unittest.TestCase):
    def test_collect_is_readonly_and_omits_wifi_credentials(self):
        def command(args,**kwargs):
            if args[0]=='nmcli':
                return 'radio-ap:wifi:connected\nentrada:ethernet:connected' if 'DEVICE,TYPE,STATE' in args else 'yes'
            if args[:3]==['ip','-j','link']:
                return '[{"ifname":"radio-ap"},{"ifname":"entrada"}]'
            if args[0]=='ip':
                return '[{"dev":"entrada","metric":5}]'
            if args==['iw','dev']:
                return 'phy#9\n Interface radio-ap\n  addr secret-not-exported\n  type managed'
            if args[0]=='iw':
                return RADIO
            if args[0]=='nft':
                return '{"nftables":[]}'
            raise AssertionError(args)
        with patch.object(t,'run_cmd',side_effect=command) as run,patch.object(t.shutil,'which',return_value='/fixture/tool'),patch.object(t,'_device_bus',return_value='usb'):
            result=t.collect_inventory()
        self.assertNotIn('secret-not-exported',json.dumps(result))
        self.assertFalse(result['network_changed'])
        self.assertEqual(result['radios']['phy9']['modes'],['managed','AP'])
        self.assertEqual(result['devices'][0]['bus'],'usb')
        for call in run.call_args_list:
            self.assertFalse(set(call.args[0]) & {'add','modify','delete','up','down','start','set','scan'})

    def test_permission_failures_remain_unknown(self):
        with patch.object(t,'run_cmd',side_effect=ThinkISPError('Permission denied')),patch.object(t.shutil,'which',return_value=None):
            result=t.collect_inventory()
        self.assertFalse(result['devices'])
        self.assertTrue(result['errors'])
        self.assertEqual(result['firewall']['status'],'unverified')


class TestProfileIntent(unittest.TestCase):
    def test_profiles_are_independent_and_other_state_is_preserved(self):
        state={'session':{'keep':True}}
        with patch.object(d,'state_lock',side_effect=lambda:nullcontext(state)),patch.object(d,'save_state'),patch.object(d,'load_state',return_value=state):
            d.save_intent(A,t.NetworkIntent(ap_interface='first-ap',portal_engine='opennds'))
            d.save_intent(B,t.NetworkIntent(ap_interface='second-ap',mode='repeater'))
            self.assertEqual(d.load_intent(A).ap_interface,'first-ap')
            self.assertEqual(d.load_intent(B).ap_interface,'second-ap')
        self.assertTrue(state['session']['keep'])
        self.assertNotIn('password',json.dumps(state))
        self.assertFalse(state['network_profiles'][A]['applied'])

    def test_absent_profile_does_not_create_files(self):
        with patch.object(d,'load_state') as load:
            self.assertEqual(d.load_intent(None),t.NetworkIntent())
        load.assert_not_called()

    def test_corrupt_or_future_config_is_never_silently_reset(self):
        for record in ({'schema_version':99,'intent':{}},{'schema_version':1,'intent':{'mode':[]}},{'schema_version':1,'intent':{'unknown':True}}):
            with patch.object(d,'load_state',return_value={'network_profiles':{A:record}}),self.assertRaises(ThinkISPError):
                d.load_intent(A)

    def test_invalid_plan_cannot_be_saved(self):
        with patch.object(d,'save_intent') as save,self.assertRaises(ThinkISPError):
            d.store_deployment({'plan':{'configuration_valid':False}})
        save.assert_not_called()

    def test_non_ap_profile_is_never_assigned_a_plan(self):
        report={'profile_uuid':A,'plan':plan()}
        with patch.object(d,'profile_values',return_value={'connection.type':'802-3-ethernet'}),patch.object(d,'save_intent') as save,self.assertRaises(ThinkISPError):
            d.store_deployment(report)
        save.assert_not_called()

    def test_nm_failure_still_returns_inventory_without_creating_profile(self):
        with patch.object(d,'profile_uuid',side_effect=ThinkISPError('NM unavailable')),patch.object(d,'collect_inventory',return_value=inventory()):
            result=d.inspect_deployment()
        self.assertIsNone(result['profile_uuid'])
        self.assertEqual(result['profile_error'],'NM unavailable')
        self.assertFalse(result['plan']['applied'])


class TestNetworkCommands(unittest.TestCase):
    def test_parser_has_no_network_detection_side_effects(self):
        from main import build_parser
        args=build_parser().parse_args(['network','plan','--ap','radio-ap','--upstream','entrada',
                                       '--mode','routed','--engine','opennds','--save','--json'])
        self.assertTrue(args.save)
        self.assertEqual(args.engine,'opennds')

    def test_readonly_json_inspection_does_not_save(self):
        import argparse,io
        from thinkisp.ui import cli
        report={'schema_version':1,'profile_uuid':A,'profile_name':'fixture','saved_intent':t.NetworkIntent().to_dict(),
                'inventory':inventory(),'plan':plan()}
        with patch.object(d,'inspect_deployment',return_value=report),patch.object(d,'store_deployment') as store,patch('sys.stdout',new_callable=io.StringIO) as output:
            cli.cmd_network(argparse.Namespace(network_action=None,json=True))
        self.assertFalse(json.loads(output.getvalue())['saved'])
        store.assert_not_called()

    def test_save_flag_only_stores_the_plan(self):
        import argparse,io
        from thinkisp.ui import cli
        report={'schema_version':1,'profile_uuid':A,'profile_name':'fixture','saved_intent':t.NetworkIntent().to_dict(),
                'inventory':inventory(),'plan':plan()}
        with patch.object(d,'inspect_deployment',return_value=report),patch.object(d,'store_deployment') as store,patch('sys.stdout',new_callable=io.StringIO) as output:
            cli.cmd_network(argparse.Namespace(network_action='plan',ap='radio-ap',upstream='entrada',mode='routed',engine='opennds',save=True,json=True))
        store.assert_called_once()
        self.assertTrue(json.loads(output.getvalue())['saved'])
        self.assertFalse(json.loads(output.getvalue())['plan']['portal_enabled'])

    def test_editor_does_not_save_when_cancelled(self):
        from thinkisp.ui import connectivity as ui
        report={'schema_version':1,'profile_uuid':A,'profile_name':'fixture','saved_intent':t.NetworkIntent(ap_interface='radio-ap').to_dict(),
                'inventory':inventory(),'plan':plan()}
        with patch.object(ui,'TerminalUI') as UI,patch.object(ui,'inspect_deployment',return_value=report),patch.object(ui,'store_deployment') as store:
            UI.return_value.work.side_effect=lambda title,operation:operation()
            UI.return_value.choose.return_value='back'
            self.assertFalse(ui.configure_connectivity(screen=object()))
        store.assert_not_called()

    def test_editor_changes_portal_engine_and_saves_without_activation(self):
        from thinkisp.ui import connectivity as ui
        report={'schema_version':1,'profile_uuid':A,'profile_name':'fixture','saved_intent':t.NetworkIntent(ap_interface='radio-ap').to_dict(),
                'inventory':inventory(),'plan':plan()}
        with patch.object(ui,'TerminalUI') as UI,patch.object(ui,'inspect_deployment',return_value=report),patch.object(ui,'store_deployment') as store:
            UI.return_value.work.side_effect=lambda title,operation:operation()
            UI.return_value.choose.side_effect=['engine','opennds','save']
            self.assertTrue(ui.configure_connectivity(screen=object()))
        saved=store.call_args.args[0]['plan']
        self.assertEqual(saved['selected']['portal_engine'],'opennds')
        self.assertFalse(saved['portal_enabled'])
