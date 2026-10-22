"""Production save path with the real nmcli parser; daemon writes are intercepted."""
import configparser
from contextlib import nullcontext
import shutil
import unittest
from unittest.mock import patch
from thinkisp.core import profile
from thinkisp.core.network import run_cmd, ThinkISPError
from thinkisp.ui.setup import BASE_FIELDS


@unittest.skipUnless(shutil.which('nmcli'), 'nmcli not installed')
class TestRealProfileSerialization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = profile._offline_template()
        cls.caps = profile.Capabilities('fixture-wifi','phy7','fixture-driver',
            [profile.Channel(6,2437,20),profile.Channel(36,5180,23)], widths=['auto','20','40','80'],security=['wpa-psk','sae'])
        cls.fields = dict(BASE_FIELDS, **{'802-11-wireless.ssid':'Prueba portátil',
                                        '802-11-wireless-security.psk':'OfflineFixturePassword'})
        cls.optional = {key:profile.probe_property(cls.template,key,value) for key,value in {
            '802-11-wireless.channel-width':'auto','802-11-wireless.ap-isolation':'1',
            '802-11-wireless-security.pmf':'2','ipv4.shared-dhcp-range':'',
            'ipv4.shared-dhcp-lease-time':'0'}.items()}

    def save(self, changes, existing=False, initial=None):
        settings=dict(self.fields)
        settings.update(changes)
        captured={'writes':[]}
        def command(args, **kwargs):
            if '--offline' in args:
                result=run_cmd(args,**kwargs)
                if 'modify' in args:
                    captured['keyfile']=result
                    captured['offline_arguments']=args[4:]
                return result
            captured['writes'].append(args)
            return ''
        with patch.object(profile,'run_cmd',side_effect=command), \
             patch.object(profile,'_offline_template',return_value=initial or self.template), \
             patch.object(profile,'profile_uuid',return_value='fixture-uuid' if existing else None), \
             patch.object(profile,'profile_values',return_value={'connection.type':'802-11-wireless','802-11-wireless.mode':'ap'}), \
             patch.object(profile,'state_lock',side_effect=lambda:nullcontext({})), \
             patch.object(profile,'save_state'):
            try:
                profile.save_profile(settings,self.caps)
            except ThinkISPError:
                self.assertFalse(captured['writes'],'A rejected profile attempted a daemon write')
                raise
        keyfile=configparser.ConfigParser(interpolation=None)
        keyfile.read_string(captured['keyfile'])
        self.assertEqual(keyfile['wifi']['mode'],'ap')
        self.assertEqual(keyfile['ipv4']['method'],'shared')
        self.assertTrue(captured['writes'])
        pairs=profile.settings_arguments(dict(settings,**{'802-11-wireless.mode':'ap','ipv4.method':'shared'}))
        self.assertEqual(captured['offline_arguments'],pairs)
        self.assertEqual(captured['writes'][-1][-len(pairs):],pairs)
        return keyfile

    def test_default_form_creates_and_edits_automatic_channel(self):
        for existing in (False,True):
            for band in ('','bg','a'):
                with self.subTest(existing=existing,band=band):
                    result=self.save({'802-11-wireless.band':band},existing)
                    self.assertEqual(result['wifi'].get('channel','0'),'0')
                    self.assertEqual(result['wifi'].get('band',''),band)

    def test_all_basic_form_values_with_installed_parser(self):
        cases=[{'802-11-wireless.hidden':value} for value in ('yes','no')]
        cases += [{'802-11-wireless.powersave':value} for value in ('0','1','2','3')]
        cases += [{'802-11-wireless.mtu':value} for value in ('0','1280','1500','9000')]
        cases += [{'ipv4.addresses':value} for value in ('','192.0.2.1/24')]
        cases += [{'802-11-wireless.band':band,'802-11-wireless.channel':channel}
                  for band,channel in (('bg','6'),('a','36'))]
        cases += [{'802-11-wireless-security.key-mgmt':'sae'}]
        for changes in cases:
            with self.subTest(changes=changes):
                self.save(changes)

    def test_optional_controls_and_reset_values(self):
        values={
            '802-11-wireless.ap-isolation':('','0','1'),
            '802-11-wireless-security.pmf':('','0','1','2','3'),
            'ipv4.shared-dhcp-lease-time':('','0','120','3600','31536000'),
            'ipv4.shared-dhcp-range':('', '192.0.2.20,192.0.2.100'),
            '802-11-wireless.channel-width':('auto','20','40','80'),
        }
        for key, choices in values.items():
            if not self.optional[key]:
                continue
            for value in choices:
                changes={key:value,'ipv4.addresses':'192.0.2.1/24'}
                if key.endswith('channel-width') and value!='auto':
                    changes.update({'802-11-wireless.band':'a','802-11-wireless.channel':'36'})
                with self.subTest(property=key,value=value):
                    self.save(changes)

    def test_reset_existing_fixed_channel_instead_of_preserving_it(self):
        fixed=run_cmd(['nmcli','--offline','connection','modify','802-11-wireless.band','a',
                       '802-11-wireless.channel','36'],input_text=self.template,check=True)
        result=self.save({'802-11-wireless.band':'bg','802-11-wireless.channel':'0'},existing=True,initial=fixed)
        self.assertEqual(result['wifi'].get('channel','0'),'0')
        self.assertEqual(result['wifi']['band'],'bg')

    def test_complete_form_with_all_supported_controls(self):
        changes={'802-11-wireless.band':'bg','802-11-wireless.channel':'6',
                 '802-11-wireless.hidden':'yes','802-11-wireless.mtu':'1500',
                 '802-11-wireless.powersave':'2','ipv4.addresses':'192.0.2.1/24'}
        for key,value in {'802-11-wireless.channel-width':'20','802-11-wireless.ap-isolation':'1',
                          '802-11-wireless-security.pmf':'2','ipv4.shared-dhcp-range':'192.0.2.20,192.0.2.100',
                          'ipv4.shared-dhcp-lease-time':'3600'}.items():
            if self.optional[key]:
                changes[key]=value
        self.save(changes)

    def test_band_command_resets_channel_with_the_real_parser(self):
        from thinkisp.core import wifi
        for band, expected in [('auto',''),('2.4g','bg'),('5g','a')]:
            captured = {}
            def command(args, **kwargs):
                captured['text'] = run_cmd(['nmcli','--offline','connection','modify',*args[5:]],
                                           input_text=self.template,check=True)
                return ''
            with self.subTest(band=band), patch.object(wifi,'inspect_capabilities',return_value=self.caps), \
                 patch.object(wifi,'run_cmd',side_effect=command), \
                 patch.object(wifi,'is_hotspot_active',return_value=False):
                self.assertTrue(wifi.set_band(band)[0])
                parsed=configparser.ConfigParser(interpolation=None)
                parsed.read_string(captured['text'])
                self.assertEqual(parsed['wifi'].get('band',''),expected)
                self.assertEqual(parsed['wifi'].get('channel','0'),'0')

    def test_invalid_values_never_reach_daemon_write(self):
        cases=[{'802-11-wireless.mtu':'-1'},{'802-11-wireless.hidden':'maybe'},
               {'802-11-wireless.powersave':'99'},{'ipv4.addresses':'not-an-address'}]
        for key,value in {'802-11-wireless.ap-isolation':'99','802-11-wireless-security.pmf':'99',
                          'ipv4.shared-dhcp-range':'not-a-range','ipv4.shared-dhcp-lease-time':'60'}.items():
            if self.optional[key]:
                cases.append({key:value})
        for changes in cases:
            with self.subTest(property=list(changes)):
                with self.assertRaises(ThinkISPError):
                    self.save(changes)
