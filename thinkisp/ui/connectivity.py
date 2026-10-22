"""Topology editor: changes intent only, never active network state."""
import curses

from thinkisp import config
from thinkisp.core.network import ThinkISPError
from thinkisp.core.deployment import inspect_deployment, report_lines, store_deployment
from thinkisp.core.topology import NetworkIntent, evaluate_plan
from thinkisp.ui.widgets import TerminalUI


def configure_connectivity(screen=None):
    def configure(win):
        ui = TerminalUI(win)
        report = ui.work('Detectando conectividad', inspect_deployment)
        intent = NetworkIntent(**report['saved_intent'])
        dirty = False
        while True:
            report['plan'] = evaluate_plan(report['inventory'], intent, fallback_ap=config.WLAN_IFACE)
            selected = report['plan']['selected']
            choice = ui.choose('Conectividad · aplicar al encender', [
                ('ap', f"Adaptador AP: {intent.ap_interface or 'Auto'} → {selected['ap_interface'] or 'Sin detectar'}"),
                ('upstream', f"Entrada de Internet: {intent.upstream_interface or 'Auto'} → {selected['upstream_interface'] or 'Sin detectar'}"),
                ('mode', f'Topología: {intent.mode} → {selected["mode"]}'),
                ('engine', f'Motor del portal previsto: {intent.portal_engine} → {selected["portal_engine"]}'),
                ('inspect', 'Ver capacidades, dependencias y restricciones'),
                ('save', 'Guardar plan por perfil · no aplica cambios de red'),
                ('back', 'Volver' + (' · hay cambios sin guardar' if dirty else '')),
            ])
            if choice in (None, 'back'):
                if not dirty or ui.choose('Hay cambios sin guardar', [('stay', 'Seguir editando'), ('discard', 'Descartar y volver')]) == 'discard':
                    return False
                continue
            try:
                if choice == 'inspect':
                    ui.message('Compatibilidad del plan', '\n'.join(report_lines(report)))
                elif choice == 'save':
                    ui.work('Guardando plan, sin modificar red', lambda: store_deployment(report))
                    return True
                else:
                    options = []
                    if choice in ('ap', 'upstream'):
                        options = [('auto', 'Automática')]
                        for device in report['inventory']['devices']:
                            if choice == 'upstream' or device['kind'] == 'wifi':
                                options.append((device['name'], f"{device['name']} · {device['kind']} · {device['state']}"))
                    elif choice == 'mode':
                        options = [('auto', 'Detectar topología'), ('routed', 'Interfaces separadas · Ethernet, USB u otra radio'),
                                   ('repeater', 'Una radio compartida · requiere concurrencia cliente/AP')]
                    elif choice == 'engine':
                        options = [('auto', 'Detectar motor instalado'), ('none', 'Sin portal'),
                                   ('opennds', 'openNDS · integración pendiente de fase 3')]
                    key = {'ap':'ap_interface', 'upstream':'upstream_interface', 'mode':'mode', 'engine':'portal_engine'}[choice]
                    current = intent.to_dict()[key]
                    value = ui.choose('Seleccionar ' + choice, options, current or 'auto')
                    if value is not None:
                        values = intent.to_dict()
                        values[key] = None if key.endswith('_interface') and value == 'auto' else value
                        intent = NetworkIntent(**values).validate()
                        dirty = True
            except ThinkISPError as exc:
                ui.message('No se guardó el plan', str(exc))
    return configure(screen) if screen is not None else curses.wrapper(configure)
