"""
ThinkISP - Demonio de Vigilancia en Segundo Plano y Temporizador
"""

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from thinkisp.config import (
    DAEMON_PID_FILE,
    HOTSPOT_CONN,
    STATE_DIR,
    TIMER_FILE,
    WLAN_IFACE,
)
from thinkisp.core.network import (
    format_duration,
    is_hotspot_active,
    notify,
    run_cmd,
)
from thinkisp.core.wifi import get_arp_map, get_hostname_for_ip


def start_daemon():
    """Inicia el demonio de vigilancia en segundo plano si no está corriendo."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if DAEMON_PID_FILE.exists():
        try:
            old_pid = int(DAEMON_PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            return
        except (OSError, ValueError):
            pass

    proc = subprocess.Popen(
        [sys.executable, "-m", "thinkisp.core.watcher"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True
    )
    DAEMON_PID_FILE.write_text(str(proc.pid))


def stop_daemon():
    """Detiene el proceso del demonio."""
    if DAEMON_PID_FILE.exists():
        try:
            pid = int(DAEMON_PID_FILE.read_text().strip())
            os.kill(pid, 15)
        except (OSError, ValueError):
            pass
        try:
            DAEMON_PID_FILE.unlink()
        except OSError:
            pass


def set_timer_countdown(total_sec):
    """Programa una cuenta regresiva para apagar el hotspot."""
    end_time = time.time() + total_sec
    TIMER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tdata = {}
    if TIMER_FILE.exists():
        try:
            tdata = json.loads(TIMER_FILE.read_text())
        except Exception:
            pass
    tdata["end_time"] = end_time
    tdata["duration"] = total_sec
    tdata["warned_5m"] = False
    TIMER_FILE.write_text(json.dumps(tdata, indent=2))
    start_daemon()

    exp_str = datetime.fromtimestamp(end_time).strftime("%H:%M:%S")
    notify("⏳ Temporizador Programado", f"El hotspot se apagará en {format_duration(total_sec)} ({exp_str}).")
    return True, f"Temporizador programado para apagar a las {exp_str} ({format_duration(total_sec)})."


def set_timer_idle(minutes):
    """Programa el apagado si pasan X minutos sin dispositivos conectados."""
    TIMER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tdata = {}
    if TIMER_FILE.exists():
        try:
            tdata = json.loads(TIMER_FILE.read_text())
        except Exception:
            pass
    tdata["idle_timeout"] = int(minutes) * 60
    TIMER_FILE.write_text(json.dumps(tdata, indent=2))
    start_daemon()

    notify("Temporizador por Inactividad", f"Apagado automático tras {minutes} min sin clientes.")
    return True, f"Apagado por inactividad configurado a {minutes} minutos."


def cancel_timer():
    """Cancela cualquier temporizador activo."""
    TIMER_FILE.unlink(missing_ok=True)
    notify("Temporizador Cancelado", "El hotspot continuará encendido.")
    return True, "Temporizador cancelado con éxito."


def get_timer_status():
    """Obtiene el estado de los temporizadores activos."""
    if not TIMER_FILE.exists():
        return None
    try:
        tdata = json.loads(TIMER_FILE.read_text())
        curr_time = time.time()
        res = {}
        if "end_time" in tdata:
            rem = tdata["end_time"] - curr_time
            if rem > 0:
                res["remaining_sec"] = rem
                res["remaining_str"] = format_duration(rem)
                res["end_time_str"] = datetime.fromtimestamp(tdata["end_time"]).strftime("%H:%M:%S")
        if "idle_timeout" in tdata:
            res["idle_mins"] = tdata["idle_timeout"] // 60
        return res
    except Exception:
        return None


def run_daemon_loop():
    """Bucle principal del demonio."""
    import re
    known_macs = set()
    initial_scan = True
    idle_seconds = 0

    while True:
        try:
            if not is_hotspot_active():
                break

            # 1. Vigilar clientes para enviar notificaciones en Hyprland
            iw_out = run_cmd(["iw", "dev", WLAN_IFACE, "station", "dump"])
            curr_macs = set()
            if iw_out:
                for line in iw_out.splitlines():
                    m = re.match(r"Station\s+([0-9a-fA-F:]{17})", line)
                    if m:
                        curr_macs.add(m.group(1).lower())

            if initial_scan:
                known_macs = curr_macs
                initial_scan = False
            else:
                new_macs = curr_macs - known_macs
                removed_macs = known_macs - curr_macs

                if new_macs:
                    time.sleep(0.8)
                    arp_map = get_arp_map()
                    for mac in new_macs:
                        ip = arp_map.get(mac, "Asignando...")
                        hostname = get_hostname_for_ip(ip)
                        notify(
                            "📱 Nuevo Dispositivo Conectado",
                            f"Se conectó: {hostname}\nIP: {ip}\nMAC: {mac}",
                            icon="network-wireless-hotspot"
                        )

                if removed_macs:
                    for mac in removed_macs:
                        notify(
                            "Dispositivo Desconectado",
                            f"Un dispositivo ha abandonado la red.\nMAC: {mac}",
                            icon="network-wireless"
                        )

                known_macs = curr_macs

            # 2. Vigilar temporizadores
            if TIMER_FILE.exists():
                try:
                    timer_data = json.loads(TIMER_FILE.read_text())
                    curr_time = time.time()
                    
                    end_time = timer_data.get("end_time")
                    if end_time:
                        rem = end_time - curr_time
                        if rem <= 0:
                            notify("⏰ Temporizador Finalizado", "El tiempo programado concluyó. Zona Wi-Fi apagada.", icon="network-wireless")
                            TIMER_FILE.unlink(missing_ok=True)
                            run_cmd(["nmcli", "connection", "down", HOTSPOT_CONN])
                            break
                        elif rem <= 300 and not timer_data.get("warned_5m"):
                            notify("⏳ Aviso de Temporizador", "La zona Wi-Fi se apagará automáticamente en 5 minutos.", icon="network-wireless-hotspot")
                            timer_data["warned_5m"] = True
                            TIMER_FILE.write_text(json.dumps(timer_data))

                    idle_timeout = timer_data.get("idle_timeout")
                    if idle_timeout:
                        if len(curr_macs) == 0:
                            idle_seconds += 2
                            if idle_seconds >= idle_timeout:
                                mins = idle_timeout // 60
                                notify("🔋 Apagado por Inactividad", f"No hubo dispositivos conectados en {mins} min. Hotspot apagado.", icon="network-wireless")
                                TIMER_FILE.unlink(missing_ok=True)
                                run_cmd(["nmcli", "connection", "down", HOTSPOT_CONN])
                                break
                        else:
                            idle_seconds = 0
                except Exception:
                    pass

            time.sleep(2.0)
        except Exception:
            time.sleep(2.0)

    stop_daemon()


if __name__ == "__main__":
    run_daemon_loop()
