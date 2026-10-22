"""thinkISP · Calidad de servicio con errores comprobados y sin perfiles de hardware."""

import math
import re

from thinkisp.config import HOTSPOT_CONN, WLAN_IFACE
from thinkisp.core.network import ThinkISPError, is_hotspot_active, load_state, notify, run_cmd, save_state, state_lock
from thinkisp.core.profile import apply_radio_settings, require_hotspot_profile


def get_gaming_mode():
    return load_state().get("gaming_mode", False)


def get_rate_limit():
    return load_state().get("rate_limit")


def _configure_qdisc(value, expected=None, powersave=None):
    active = is_hotspot_active(check=True)
    args = ["nmcli", "connection", "modify", "id", HOTSPOT_CONN, "tc.qdiscs", value]
    if powersave is not None:
        args += ["802-11-wireless.powersave", powersave]
    run_cmd(args, check=True)
    if active:
        run_cmd(["nmcli", "--wait", "60", "connection", "up", "id", HOTSPOT_CONN], check=True, timeout=65)
        apply_radio_settings(require_hotspot_profile())
        actual = run_cmd(["tc", "qdisc", "show", "dev", WLAN_IFACE], check=True)
        kinds = re.findall(r"qdisc (\S+) .*?\broot\b", actual)
        if expected and expected not in kinds:
            raise ThinkISPError(f"La cola {expected} no se encontró en {WLAN_IFACE} después de aplicar el perfil.")
        if not expected and any(kind in ("cake", "tbf") for kind in kinds):
            raise ThinkISPError(f"La cola anterior continúa aplicada en {WLAN_IFACE}.")
    return active


def set_gaming_mode(enabled):
    active = _configure_qdisc("root cake" if enabled else "", "cake" if enabled else None)
    with state_lock() as state:
        state["gaming_mode"] = bool(enabled)
        state.pop("rate_limit", None)
        save_state(state)
    verb = "activado" if active else "configurado para el próximo encendido"
    message = f"CAKE {verb}." if enabled else "Colas estándar restauradas en el perfil."
    notify("thinkISP · QoS", message)
    return True, message


def set_rate_limit(mbps):
    try:
        rate = float(mbps)
    except (TypeError, ValueError) as exc:
        raise ThinkISPError("El límite debe ser una velocidad numérica positiva.") from exc
    if not math.isfinite(rate) or rate <= 0:
        raise ThinkISPError("El límite debe ser una velocidad finita mayor que cero.")
    rate_bytes = int(rate * 1_000_000 / 8)
    active = _configure_qdisc(f"root tbf burst 32768 latency 50 rate {rate_bytes}", "tbf")
    with state_lock() as state:
        state["rate_limit"] = f"{rate:g} Mbps"
        state["gaming_mode"] = False
        save_state(state)
    message = f"Límite de {rate:g} Mbps {'aplicado' if active else 'guardado para el próximo encendido'}."
    notify("thinkISP · Ancho de banda", message)
    return True, message


def clear_rate_limit():
    _configure_qdisc("")
    with state_lock() as state:
        state.pop("rate_limit", None)
        state["gaming_mode"] = False
        save_state(state)
    return True, "Límite de velocidad eliminado del perfil."


def apply_boost_mode():
    # No cambia banda, canal, potencia, identidad ni perfil de CPU del equipo.
    active = _configure_qdisc("root cake", "cake", powersave="2")
    with state_lock() as state:
        state["gaming_mode"] = True
        state.pop("rate_limit", None)
        save_state(state)
    message = "CAKE y ahorro de energía Wi-Fi desactivado: " + ("aplicados." if active else "guardados para el próximo encendido.")
    notify("thinkISP · Boost", message)
    return True, message


def enable_gaming_mode():
    return set_gaming_mode(True)


def disable_gaming_mode():
    return set_gaming_mode(False)


def apply_rate_limit(mbps):
    return set_rate_limit(mbps)


def get_current_qos():
    state = load_state()
    return {"gaming_mode": state.get("gaming_mode", False), "rate_limit": state.get("rate_limit")}
