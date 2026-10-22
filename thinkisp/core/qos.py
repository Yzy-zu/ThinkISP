"""
ThinkISP - Módulo de Calidad de Servicio (QoS), CAKE y Traffic Shaping
"""

from pathlib import Path
import shutil

from thinkisp.config import HOTSPOT_CONN, THINKPAD_MODE_BIN
from thinkisp.core.network import (
    is_hotspot_active,
    load_state,
    notify,
    run_cmd,
    save_state,
)
from thinkisp.core.wifi import apply_zero_latency_optimizations


def get_gaming_mode():
    """Consulta si el modo Gaming (CAKE) está activo."""
    state = load_state()
    return state.get("gaming_mode", False)


def set_gaming_mode(enabled):
    """Activa o desactiva colas inteligentes CAKE y prioridad UDP."""
    state = load_state()
    if enabled:
        run_cmd([
            "nmcli", "connection", "modify", HOTSPOT_CONN,
            "tc.qdiscs", "root cake",
            "802-11-wireless.powersave", "2"
        ])
        apply_zero_latency_optimizations()

        if THINKPAD_MODE_BIN.exists():
            try:
                run_cmd([str(THINKPAD_MODE_BIN), "performance"])
            except Exception:
                pass

        if is_hotspot_active():
            run_cmd(["nmcli", "connection", "up", HOTSPOT_CONN])
            apply_zero_latency_optimizations()

        state["gaming_mode"] = True
        save_state(state)
        notify("🎮 Modo Gaming Activado", "Prioridad UDP | CAKE Anti-Bufferbloat | 0 Latencia")
        return True, "Modo Gaming (CAKE) activado."
    else:
        run_cmd(["nmcli", "connection", "modify", HOTSPOT_CONN, "tc.qdiscs", ""])
        state["gaming_mode"] = False
        save_state(state)

        if is_hotspot_active():
            run_cmd(["nmcli", "connection", "up", HOTSPOT_CONN])
            apply_zero_latency_optimizations()

        notify("Modo Gaming Desactivado", "Colas estándar de red restauradas.")
        return True, "Modo Gaming desactivado."


def get_rate_limit():
    """Obtiene el límite de velocidad actual configurado."""
    state = load_state()
    return state.get("rate_limit")


def set_rate_limit(mbps):
    """Aplica control de tasa TBF (Token Bucket Filter) en el hotspot."""
    state = load_state()
    rate_bytes = int((float(mbps) * 1_000_000) / 8)

    run_cmd([
        "nmcli", "connection", "modify", HOTSPOT_CONN,
        "tc.qdiscs", f"root tbf burst 32768 latency 50 rate {rate_bytes}"
    ])

    state["rate_limit"] = f"{mbps} Mbps"
    save_state(state)

    if is_hotspot_active():
        run_cmd(["nmcli", "connection", "up", HOTSPOT_CONN])
        apply_zero_latency_optimizations()

    notify("Límite de Ancho de Banda", f"Zona Wi-Fi limitada a {mbps} Mbps.")
    return True, f"Límite de ancho de banda fijado en {mbps} Mbps."


def clear_rate_limit():
    """Elimina el límite de velocidad del hotspot."""
    state = load_state()
    run_cmd(["nmcli", "connection", "modify", HOTSPOT_CONN, "tc.qdiscs", ""])
    state.pop("rate_limit", None)
    save_state(state)

    if is_hotspot_active():
        run_cmd(["nmcli", "connection", "up", HOTSPOT_CONN])
        apply_zero_latency_optimizations()

    notify("Límite Eliminado", "Transmisión a velocidad completa.")
    return True, "Límite de velocidad eliminado."


def apply_boost_mode():
    """Aplica todas las optimizaciones para exprimir la línea Gigabit."""
    run_cmd([
        "nmcli", "connection", "modify", HOTSPOT_CONN,
        "802-11-wireless.band", "a",
        "802-11-wireless.channel", "149",
        "802-11-wireless.powersave", "2",
        "tc.qdiscs", "root cake"
    ])

    apply_zero_latency_optimizations()
    state = load_state()
    state["gaming_mode"] = True
    save_state(state)

    if THINKPAD_MODE_BIN.exists():
        try:
            status_json = run_cmd([str(THINKPAD_MODE_BIN), "status"])
            if status_json:
                import json
                data = json.loads(status_json)
                curr_profile = data.get("profile")
                if curr_profile and curr_profile != "performance":
                    state["prev_tp_mode"] = curr_profile
                    save_state(state)
            run_cmd([str(THINKPAD_MODE_BIN), "performance"])
        except Exception:
            pass

    notify("🚀 ISP BOOST Activado", "5GHz 80MHz | CAKE | 0 Latencia | ThinkPad Nuclear")
    return True, "Modo Turbo Boost activado con éxito."


def enable_gaming_mode():
    """Activa colas inteligentes CAKE y prioridad UDP anti-jitter."""
    return set_gaming_mode(True)


def disable_gaming_mode():
    """Desactiva colas CAKE restaurando colas estándar."""
    return set_gaming_mode(False)


def apply_rate_limit(mbps):
    """Aplica control de tasa TBF (Token Bucket Filter) en el hotspot."""
    return set_rate_limit(mbps)


def get_current_qos():
    """Obtiene el estado completo de calidad de servicio y límites."""
    state = load_state()
    return {
        "gaming_mode": state.get("gaming_mode", False),
        "rate_limit": state.get("rate_limit", None)
    }


def set_thinkpad_performance():
    """Configura el perfil de máximo rendimiento de forma universal (thinkpad-mode / powerprofilesctl / sysfs)."""
    # 1. thinkpad-mode si está presente en la laptop ThinkPad
    if THINKPAD_MODE_BIN.exists():
        try:
            run_cmd([str(THINKPAD_MODE_BIN), "performance"])
            return True
        except Exception:
            pass

    # 2. freedesktop power-profiles-daemon (Estándar en Ubuntu, Fedora, Debian, Arch con GNOME o KDE)
    if shutil.which("powerprofilesctl"):
        try:
            run_cmd(["powerprofilesctl", "set", "performance"])
            return True
        except Exception:
            pass

    # 3. sysfs cpufreq governor directo
    try:
        for p in Path("/sys/devices/system/cpu").glob("cpu*/cpufreq/scaling_governor"):
            p.write_text("performance")
        return True
    except Exception:
        pass

    return False
