"""
ThinkISP - Módulo de DNS, AdBlock y Medición de Velocidad
"""

import re
import subprocess
import time

from thinkisp.config import DNS_PROVIDERS
from thinkisp.core.network import (
    apply_upstream_dns,
    load_state,
    notify,
    run_cmd,
    save_state,
)


def get_current_dns():
    """Obtiene el nombre del proveedor DNS activo."""
    state = load_state()
    return state.get("dns", "Auto / DHCP")


def get_adblock_status():
    """Comprueba si el bloqueo de anuncios a nivel de DNS está activo."""
    state = load_state()
    return state.get("adblock", False)


def set_dns_provider(key):
    """Establece el proveedor de DNS para conexiones salientes."""
    key = key.lower()
    state = load_state()

    if key in ("auto", "dhcp", "reset", "default"):
        apply_upstream_dns(None)
        state["dns"] = "Auto / DHCP"
        state["adblock"] = False
        save_state(state)
        notify("DNS Automático", "Usando los servidores del router.")
        return True, "DNS restaurado a automático (DHCP)."

    if key in DNS_PROVIDERS:
        prov = DNS_PROVIDERS[key]
        apply_upstream_dns(prov["servers"])
        state["dns"] = prov["name"]
        state["adblock"] = (key == "adguard")
        save_state(state)
        notify("DNS Actualizado", f"Usando: {prov['name']}")
        return True, f"DNS cambiado a {prov['name']}."

    return False, f"Proveedor desconocido: '{key}'."


def set_adblock(enabled):
    """Activa o desactiva AdGuard DNS en la red."""
    if enabled:
        return set_dns_provider("adguard")
    else:
        return set_dns_provider("auto")


def benchmark_dns():
    """Ejecuta una prueba de resolución con dig para medir latencia en milisegundos."""
    results = []
    for key, info in DNS_PROVIDERS.items():
        ip = info["ip_test"]
        out = run_cmd(["dig", f"@{ip}", "google.com", "+time=2", "+tries=1"])
        q_time = None
        if out:
            m = re.search(r"Query time:\s*(\d+)\s*msec", out)
            if m:
                q_time = int(m.group(1))

        results.append({
            "key": key,
            "name": info["name"],
            "ip": ip,
            "time": q_time,
            "desc": info["desc"]
        })

    results.sort(key=lambda x: (x["time"] is None, x["time"]))
    return results


def run_speedtest():
    """Mide ping, descarga (25MB) y subida (5MB) contra la red de Cloudflare."""
    metrics = {
        "ping_ms": None,
        "down_mbps": None,
        "down_mbs": None,
        "up_mbps": None,
        "up_mbs": None,
        "error": None
    }

    # 1. Ping
    try:
        lat_res = subprocess.run([
            "curl", "-s", "-w", "%{time_connect}", "-o", "/dev/null",
            "https://speed.cloudflare.com/__down?bytes=0"
        ], capture_output=True, text=True, timeout=6)
        metrics["ping_ms"] = float(lat_res.stdout.strip()) * 1000
    except Exception as e:
        metrics["error"] = f"Error en ping: {e}"

    # 2. Descarga (25 MB)
    try:
        down_res = subprocess.run([
            "curl", "-s", "-w", "%{time_total} %{size_download}", "-o", "/dev/null",
            "https://speed.cloudflare.com/__down?bytes=25000000"
        ], capture_output=True, text=True, timeout=18)
        t_down, sz_down = map(float, down_res.stdout.strip().split())
        metrics["down_mbps"] = (sz_down * 8) / (t_down * 1_000_000)
        metrics["down_mbs"] = (sz_down / (1024 * 1024)) / t_down
    except Exception as e:
        metrics["error"] = f"Error en descarga: {e}"

    # 3. Subida (5 MB)
    try:
        up_res = subprocess.run([
            "curl", "-s", "-w", "%{time_total} %{size_upload}", "-o", "/dev/null",
            "-X", "POST", "--data-binary", "@-",
            "https://speed.cloudflare.com/__up"
        ], input="0" * (5 * 1024 * 1024), capture_output=True, text=True, timeout=18)
        t_up, sz_up = map(float, up_res.stdout.strip().split())
        metrics["up_mbps"] = (sz_up * 8) / (t_up * 1_000_000)
        metrics["up_mbs"] = (sz_up / (1024 * 1024)) / t_up
    except Exception as e:
        metrics["error"] = f"Error en subida: {e}"

    return metrics
