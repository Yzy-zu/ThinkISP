"""Per-profile network intent. Inspection and intent storage never mutate the live network."""
from dataclasses import fields
import uuid

from thinkisp import config
from thinkisp.core.network import ThinkISPError, load_state, save_state, state_lock
from thinkisp.core.profile import profile_uuid, profile_values
from thinkisp.core.topology import NetworkIntent, collect_inventory, evaluate_plan


def load_intent(profile):
    if profile is None:
        return NetworkIntent()
    records = load_state().get('network_profiles', {})
    if not isinstance(records, dict):
        raise ThinkISPError('La configuración de conectividad está dañada; no se reemplazará.')
    record = records.get(profile)
    if record is None:
        return NetworkIntent()
    if not isinstance(record, dict) or record.get('schema_version') != 1:
        raise ThinkISPError('Versión de configuración de conectividad no compatible.')
    values = record.get('intent')
    if not isinstance(values, dict) or set(values) - {f.name for f in fields(NetworkIntent)}:
        raise ThinkISPError('El plan de conectividad contiene campos no reconocidos.')
    return NetworkIntent(**values).validate()


def save_intent(profile, intent):
    intent.validate()
    try:
        uuid.UUID(profile)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ThinkISPError('Configura primero un perfil AP con thinkisp setup.') from exc
    with state_lock() as state:
        if state.get('connectivity_runtime', {}).get('phase', 'idle') != 'idle':
            raise ThinkISPError('Apaga o recupera la conectividad antes de cambiar el plan.')
        records = state.setdefault('network_profiles', {})
        if not isinstance(records, dict):
            raise ThinkISPError('La configuración existente de conectividad está dañada.')
        records[profile] = {'schema_version': 1, 'intent': intent.to_dict(), 'applied': False}
        save_state(state)


def inspect_deployment(intent=None):
    profile_error = None
    try:
        profile = profile_uuid()
    except ThinkISPError as exc:
        profile, profile_error = None, str(exc)
    saved = load_intent(profile)
    intent = intent or saved
    inventory = collect_inventory()
    plan = evaluate_plan(inventory, intent, fallback_ap=config.WLAN_IFACE)
    runtime = load_state().get('connectivity_runtime', {})
    runtime = runtime if runtime.get('owner') == profile else {}
    from thinkisp.core import portal
    portal_state = portal.status(profile) if profile else {'enabled': False, 'phase': 'disabled'}
    return {'portal': portal_state, 'runtime': {key: runtime[key] for key in ('phase', 'ap_interface', 'upstream_interface', 'mode') if key in runtime}, 'schema_version': 1, 'profile_uuid': profile, 'profile_name': config.HOTSPOT_CONN,
            'saved_intent': saved.to_dict(), 'inventory': inventory, 'plan': plan, 'profile_error': profile_error}


def store_deployment(report):
    if not report['plan']['configuration_valid']:
        raise ThinkISPError('El plan tiene incompatibilidades; revisa los errores antes de guardarlo.')
    profile = report['profile_uuid']
    if profile is None:
        raise ThinkISPError('Configura primero el perfil AP con thinkisp setup; inspeccionar no crea perfiles.')
    kind = profile_values(['connection.type', '802-11-wireless.mode'], profile, check=True)
    if kind.get('connection.type') != '802-11-wireless' or kind.get('802-11-wireless.mode') != 'ap':
        raise ThinkISPError('El perfil seleccionado no es un punto de acceso; no se guardó el plan.')
    save_intent(profile, NetworkIntent(**report['plan']['intent']))


def report_lines(report):
    selected, plan = report['plan']['selected'], report['plan']
    lines = [f"Perfil: {report['profile_name']}",
             f"AP: {selected['ap_interface'] or 'Sin detectar'}",
             f"Entrada de Internet: {selected['upstream_interface'] or 'Sin detectar'}",
             f"Topología: {selected['mode']}", f"Motor previsto: {selected['portal_engine']}",
             f"Ejecución: {report.get('runtime', {}).get('phase', 'sin aplicar')}. Portal: {report.get('portal', {}).get('phase', 'disabled')}.", '', 'Interfaces detectadas:']
    for device in report['inventory']['devices']:
        lines.append(f"  {device['name']} · {device['kind']} · {device['state']} · bus {device['bus']}" +
                     (f" · {device['phy']}" if device.get('phy') else ''))
    lines += ['', 'Comprobaciones:']
    if report.get('profile_error'):
        lines.append('  PERFIL SIN CONFIRMAR: ' + report['profile_error'])
    for item in plan['issues']:
        lines.append(f"  {item['level'].upper()}: {item['detail']}")
    for item in report['inventory']['errors']:
        lines.append('  SIN CONFIRMAR: ' + item['detail'])
    lines.append('La consulta no prueba navegación ni modifica interfaces, perfiles o reglas.')
    return lines
