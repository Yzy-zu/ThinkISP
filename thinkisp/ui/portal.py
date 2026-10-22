"""Terminal UI for captive portal management and client authorization."""
import curses
import json

from thinkisp.core.profile import profile_uuid
from thinkisp.core.network import ThinkISPError, is_hotspot_active
from thinkisp.core.wifi import get_connected_clients
from thinkisp.ui.widgets import TerminalUI
from thinkisp import config


def get_portal_backend():
    from thinkisp.core import portal
    return portal


def manage_portal(screen=None):
    def run(win):
        ui = TerminalUI(win)
        owner = profile_uuid()
        if not owner:
            ui.message('Portal cautivo', 'No hay un perfil de punto de acceso configurado. Configura el punto de acceso primero con "thinkisp setup".')
            return

        backend = get_portal_backend()
        while True:
            try:
                st = ui.work('Consultando portal', lambda: backend.status(owner=owner))
            except (ThinkISPError, OSError) as exc:
                ui.message('Error', f"No se pudo consultar el estado del portal: {exc}")
                return

            enabled = bool(st.get('enabled', False))
            engine = st.get('engine', 'builtin')
            phase = st.get('phase', 'disabled')
            phase_label = {'disabled':'Desactivado', 'configured':'Listo para el próximo encendido',
                           'preparing':'Iniciando', 'prepared':'Preparado', 'running':'Activo',
                           'recovering':'Recuperando', 'recovery_pending':'Recuperación pendiente'}.get(phase, phase)
            engine_label = 'Integrado' if engine == 'builtin' else engine
            url = st.get('url') if phase in ('ready', 'active', 'running') else None
            auth_list = [c for c in st.get('clients', [])
                         if isinstance(c, dict) and c.get('authorized') is True]

            hotspot_on = is_hotspot_active(check=False)
            url_display = url if url else 'No disponible'

            choices = [
                ('state', f"Estado: {phase_label} · {url_display}"),
                ('toggle', f"Portal: {'Habilitado' if enabled else 'Desactivado'} (aplicar al encendido)"),
                ('authorized', f"Ver clientes autorizados ({len(auth_list)})"),
                ('authorize', "Autorizar dispositivo (IP o MAC)"),
                ('revoke', "Revocar autorización de dispositivo"),
                ('details', "Ver diagnóstico y detalles"),
                ('back', "Volver al panel"),
            ]

            choice = ui.choose('Portal cautivo', choices)
            if choice in (None, 'back'):
                return

            try:
                if choice == 'state':
                    info = [
                        f"Perfil: {config.HOTSPOT_CONN}",
                        f"Habilitado: {'Sí' if enabled else 'No'}",
                        f"Motor: {engine_label}",
                        f"Estado: {phase_label}",
                        f"Punto de acceso activo: {'Sí' if hotspot_on else 'No'}",
                        f"URL del portal: {url or 'No disponible (requiere portal activo)'}",
                        f"Clientes autorizados: {len(auth_list)}",
                    ]
                    if st.get('diagnostic'):
                        info.append(f"\nDiagnóstico:\n{st.get('diagnostic')}")
                    ui.message('Estado del portal', '\n'.join(info))

                elif choice == 'toggle':
                    if hotspot_on:
                        ui.message('Configuración del portal',
                                   'El punto de acceso está activo. Debes apagarlo con "thinkisp off" o desde el panel antes de habilitar o desactivar el portal cautivo.')
                    else:
                        new_state = not enabled
                        action_label = 'Habilitando portal' if new_state else 'Desactivando portal'
                        ui.work(action_label, lambda: backend.configure(owner, new_state))
                        ui.message('Configuración guardada',
                                   f"Portal cautivo {'habilitado' if new_state else 'desactivado'}. Se aplicará en el próximo encendido.")

                elif choice == 'authorized':
                    if not auth_list:
                        ui.message('Clientes autorizados', 'No hay clientes autorizados en este momento.')
                    else:
                        lines = ["Dispositivos con acceso autorizado por el portal:"]
                        for item in auth_list:
                            if isinstance(item, dict):
                                mac = item.get('mac', '')
                                ip = item.get('ip', '')
                                lines.append(f"  • {ip:<15}  {mac}")
                            else:
                                lines.append(f"  • {item}")
                        lines.append("\nNota: La revocación permite que el cliente vuelva a pasar por el portal.")
                        ui.message('Clientes autorizados', '\n'.join(lines))

                elif choice == 'authorize':
                    conn = get_connected_clients(check=False)
                    client_choices = []
                    for c in conn:
                        client_choices.append((c.get('ip') or c.get('mac'), f"{c.get('hostname') or 'dispositivo'} · {c.get('ip')} · {c.get('mac')}"))
                    client_choices.append(('manual', 'Escribir dirección IP o MAC manualmente…'))
                    client_choices.append(('back', 'Cancelar'))

                    pick = ui.choose('Autorizar dispositivo', client_choices)
                    if pick in (None, 'back'):
                        continue
                    if pick == 'manual':
                        target = ui.edit('Autorizar dispositivo', hint='Dirección IP o MAC a autorizar')
                        if not target or not target.strip():
                            continue
                        target = target.strip()
                    else:
                        target = pick

                    ok, msg = ui.work('Autorizando cliente', lambda: backend.authorize(owner, target))
                    if not ok:
                        raise ThinkISPError(str(msg))
                    ui.message('Autorización', msg)

                elif choice == 'revoke':
                    revoke_choices = []
                    for item in auth_list:
                        val = item.get('mac') or item.get('ip') if isinstance(item, dict) else str(item)
                        label = f"{item.get('ip', '')} · {item.get('mac', '')}" if isinstance(item, dict) else str(item)
                        revoke_choices.append((val, label))
                    revoke_choices.append(('manual', 'Escribir dirección IP o MAC manualmente…'))
                    revoke_choices.append(('back', 'Cancelar'))

                    pick = ui.choose('Revocar dispositivo', revoke_choices)
                    if pick in (None, 'back'):
                        continue
                    if pick == 'manual':
                        target = ui.edit('Revocar dispositivo', hint='Dirección IP o MAC a revocar')
                        if not target or not target.strip():
                            continue
                        target = target.strip()
                    else:
                        target = pick

                    ok, msg = ui.work('Revocando autorización', lambda: backend.revoke(owner, target))
                    if not ok:
                        raise ThinkISPError(str(msg))
                    ui.message('Revocación', msg)

                elif choice == 'details':
                    diag = st.get('diagnostic')
                    diag_str = json.dumps(diag, ensure_ascii=False, indent=2) if isinstance(diag, (dict, list)) else str(diag or 'Sin observaciones adicionales')
                    ui.message('Detalles del portal', f"Motor: {engine}\nFase: {phase}\nURL: {url or 'Inactiva'}\n\nDiagnóstico:\n{diag_str}")

            except (ThinkISPError, OSError) as exc:
                ui.message('Error', str(exc))

    return run(screen) if screen is not None else curses.wrapper(run)
