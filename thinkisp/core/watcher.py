"""
ThinkISP - Demonio de Vigilancia en Segundo Plano y Temporizador
"""

from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from thinkisp.config import ap_interface
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
    ThinkISPError,
)
from thinkisp.core.wifi import get_arp_map, get_hostname_for_ip, stop_hotspot


def start_daemon():
    """Inicia el demonio de vigilancia en segundo plano si no está corriendo."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if DAEMON_PID_FILE.exists():
        try:
            old_pid = int(DAEMON_PID_FILE.read_text().strip())
            os.kill(old_pid, 0)
            if b'thinkisp.core.watcher' in Path(f'/proc/{old_pid}/cmdline').read_bytes():
                return
        except (OSError, ValueError):
            pass

    proc = subprocess.Popen(
        [sys.executable, "-m", "thinkisp.core.watcher"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, 'THINKISP_NO_AUTH': '1'},
    )
    DAEMON_PID_FILE.write_text(str(proc.pid))


def stop_daemon():
    """Detiene el proceso del demonio."""
    if DAEMON_PID_FILE.exists():
        try:
            pid = int(DAEMON_PID_FILE.read_text().strip())
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
            if b"thinkisp.core.watcher" in command and pid != os.getpid():
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
    notify("Temporizador Cancelado", "Se eliminó el apagado programado.")
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


class HotspotWatcher:
    """Clase para monitorear eventos del hotspot (conexión/desconexión de clientes y temporizadores)."""

    def __init__(self, check_interval=2.0, timer_file=TIMER_FILE):
        self.check_interval = check_interval
        self.timer_file = timer_file
        self.known_macs = set()
        self.initial_scan = True
        self.idle_seconds = 0
        self.running = False
        self.on_client_connected = []
        self.on_client_disconnected = []
        self.on_timer_warning = []
        self.on_timer_expired = []
        self.on_idle_expired = []

    def scan_clients(self):
        """Escanea estaciones conectadas y detecta clientes nuevos o desconectados."""
        iw_out = run_cmd(["iw", "dev", ap_interface(WLAN_IFACE), "station", "dump"], check=True)
        curr_macs = set()
        if iw_out:
            for line in iw_out.splitlines():
                m = re.match(r"Station\s+([0-9a-fA-F:]{17})", line)
                if m:
                    curr_macs.add(m.group(1).lower())

        events = []
        if self.initial_scan:
            self.known_macs = curr_macs
            self.initial_scan = False
        else:
            new_macs = curr_macs - self.known_macs
            removed_macs = self.known_macs - curr_macs

            if new_macs:
                arp_map = get_arp_map()
                for mac in new_macs:
                    ip = arp_map.get(mac, "Asignando...")
                    hostname = get_hostname_for_ip(ip)
                    event = {"type": "client_connected", "mac": mac, "ip": ip, "hostname": hostname}
                    events.append(event)
                    for cb in self.on_client_connected:
                        cb(event)
                    notify(
                        "📱 Nuevo Dispositivo Conectado",
                        f"Se conectó: {hostname}\nIP: {ip}\nMAC: {mac}",
                        icon="network-wireless-hotspot"
                    )

            if removed_macs:
                for mac in removed_macs:
                    event = {"type": "client_disconnected", "mac": mac}
                    events.append(event)
                    for cb in self.on_client_disconnected:
                        cb(event)
                    notify(
                        "Dispositivo Desconectado",
                        f"Un dispositivo ha abandonado la red.\nMAC: {mac}",
                        icon="network-wireless"
                    )

            self.known_macs = curr_macs

        return events

    def check_timers(self):
        """Comprueba temporizadores de cuenta regresiva e inactividad."""
        if not self.timer_file.exists():
            return None

        try:
            timer_data = json.loads(self.timer_file.read_text())
            curr_time = time.time()
            res = {"action": None}

            end_time = timer_data.get("end_time")
            if end_time:
                rem = end_time - curr_time
                if rem <= 0:
                    stop_hotspot()
                    res["action"] = "shutdown_timer"
                    for cb in self.on_timer_expired:
                        cb(timer_data)
                    notify("⏰ Temporizador Finalizado", "El tiempo programado concluyó. Zona Wi-Fi apagada.", icon="network-wireless")
                    self.timer_file.unlink(missing_ok=True)
                    return res
                elif rem <= 300 and not timer_data.get("warned_5m"):
                    res["action"] = "warn_5m"
                    for cb in self.on_timer_warning:
                        cb(rem)
                    notify("⏳ Aviso de Temporizador", "La zona Wi-Fi se apagará automáticamente en 5 minutos.", icon="network-wireless-hotspot")
                    timer_data["warned_5m"] = True
                    self.timer_file.write_text(json.dumps(timer_data))

            idle_timeout = timer_data.get("idle_timeout")
            if idle_timeout:
                if len(self.known_macs) == 0:
                    self.idle_seconds += self.check_interval
                    if self.idle_seconds >= idle_timeout:
                        stop_hotspot()
                        mins = idle_timeout // 60
                        res["action"] = "shutdown_idle"
                        for cb in self.on_idle_expired:
                            cb(timer_data)
                        notify("🔋 Apagado por Inactividad", f"No hubo dispositivos conectados en {mins} min. Hotspot apagado.", icon="network-wireless")
                        self.timer_file.unlink(missing_ok=True)
                        return res
                else:
                    self.idle_seconds = 0

            return res
        except ThinkISPError as exc:
            notify("thinkISP · Error de temporizador", str(exc), urgency="critical")
            return {"action": "error", "error": str(exc)}
        except Exception:
            return None

    def tick(self):
        """Ejecuta un ciclo de escaneo de clientes y verificación de temporizadores."""
        from thinkisp.core.connectivity import health_check
        try:
            health_check()
        except ThinkISPError as exc:
            stop_hotspot()
            notify('thinkISP · Entrada desconectada', str(exc), urgency='critical')
            return {'client_events': [], 'timer_result': {'action': 'shutdown_upstream'}}
        client_events = self.scan_clients()
        timer_result = self.check_timers()
        return {
            "client_events": client_events,
            "timer_result": timer_result
        }


def run_daemon_loop():
    """Bucle principal del demonio."""
    watcher = HotspotWatcher(check_interval=2.0)

    while True:
        try:
            if not is_hotspot_active(check=True):
                try:
                    stop_hotspot()
                except ThinkISPError as exc:
                    notify('thinkISP · Recuperación pendiente', str(exc), urgency='critical')
                break

            tick_res = watcher.tick()
            if tick_res.get("timer_result") and tick_res["timer_result"].get("action") in ("shutdown_timer", "shutdown_idle", "shutdown_upstream"):
                break

            time.sleep(2.0)
        except Exception:
            time.sleep(2.0)

    # La salida natural limpia el archivo; no enviar SIGTERM al propio proceso.
    try:
        if DAEMON_PID_FILE.read_text().strip() == str(os.getpid()):
            DAEMON_PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


if __name__ == "__main__":
    run_daemon_loop()
