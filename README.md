# ⚡ ThinkISP - Portable High-Performance Router & Hotspot Suite for Linux

<div align="center">

```text
  ████████╗██╗  ██╗██╗███╗   ██╗██╗  ██╗██╗███████╗██████╗ 
  ╚══██╔══╝██║  ██║██║████╗  ██║██║ ██╔╝██║██╔════╝██╔══██╗
     ██║   ███████║██║██╔██╗ ██║█████╔╝ ██║███████╗██████╔╝
     ██║   ██╔══██║██║██║╚██╗██║██╔═██╗ ██║╚════██║██╔═══╝ 
     ██║   ██║  ██║██║██║ ╚████║██║  ██╗██║███████║██║     
     ╚═╝   ╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝╚══════╝╚═╝     
```

**Convierte tu laptop Linux / ThinkPad en un Router de Alto Rendimiento (Gigabit Ethernet ➔ Wi-Fi 5GHz VHT80)**  
*Zero-Latency | Bufferbloat Elimination (CAKE) | Network AdBlock | Interactive TUI | CLI Automation*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux-orange.svg)]()
[![Python: 3.9+](https://img.shields.io/badge/Python-3.9+-brightgreen.svg)]()
[![No Dependencies](https://img.shields.io/badge/Dependencies-Zero-success.svg)]()

</div>

---

## 📖 Descripción / Description

**ThinkISP** es una suite completa diseñada para compartir internet desde tu laptop Linux (por ejemplo, conectada al cable Ethernet Gigabit de la escuela, universidad u oficina) hacia tus celulares, tablets y dispositivos móviles a través de Wi-Fi, exprimiendo hasta el último megabit y eliminando la latencia.

Cuenta con una **interfaz interactiva TUI** (en terminal, navegable con teclado y ratón) y un conjunto de **comandos CLI** listos para integrar en barras como Waybar, QuickShell o atajos de teclado de Hyprland.

---

## 🚀 Características Principales / Key Features

* **⚡ Rendimiento Gigabit (5 GHz VHT80)**: Fuerza la antena a 5 GHz en el canal 149 con 80 MHz de ancho de banda, alcanzando tasas de enlace de hasta **867 Mbps PHY** en tarjetas Intel Dual Band Wireless-AC.
* **🎯 Cero Latencia & Anti-Jitter**: Desactiva el ahorro de energía de la antena Wi-Fi (`power_save off`) para garantizar pings planos de **< 2 ms** sin micro-cortes.
* **🎮 Modo Gaming & Anti-Bufferbloat (CAKE)**: Algoritmo de colas inteligentes **CAKE** en el kernel que prioriza paquetes de juegos, VoIP y Discord sobre descargas pesadas o streams 4K.
* **🛡️ Bloqueador de Anuncios Universal (AdBlock DNS)**: Bloquea anuncios, ventanas emergentes y rastreadores en **todos los celulares y dispositivos conectados** sin instalar nada en los teléfonos.
* **🌐 Selector & Benchmark de DNS**: Mide en tiempo real la latencia de Google, Cloudflare, Quad9 y AdGuard, permitiendo alternar al proveedor más rápido con una sola tecla.
* **📱 Vigilante de Red & Notificaciones de Hyprland**: Te avisa mediante `notify-send` cada vez que un nuevo dispositivo se conecta o desconecta con su nombre de host e IP.
* **🚫 Expulsión y Lista Negra Permanente (`kick / unkick`)**: Expulsa intrusos por IP o MAC al vuelo sin necesidad de apagar la zona Wi-Fi.
* **🕵️ Modo Stealth (SSID Oculto)**: Oculta el nombre de la red de las balizas públicas de radio.
* **⏳ Temporizador Inteligente**: Programa el apagado automático al finalizar tu clase (ej. `45m`) o por inactividad si no hay clientes conectados.
* **📊 Telemetría en Vivo & Test de Velocidad**: Monitor de tráfico interactivo en tiempo real por cliente y speedtest integrado sin librerías externas.
* **📱 Código QR en Terminal**: Muestra un código QR Unicode en pantalla para que tus compañeros apunten con la cámara del celular y se conecten al instante.

---

## 🖥️ Interfaz Interactiva (TUI)

Ejecuta simplemente `thinkisp` (o `isp`) sin argumentos para abrir el Dashboard interactivo:

```text
┌─── [ THINKISP: SPUTNIK HOTSPOT MANAGER ] ──────────────────────────────────────────┐
│  ESTADO: ● ACTIVO   │  BANDA: 5 GHz (Ch 149 / 80MHz)  │  ETHERNET: Gigabit (1000M) │
│  SSID: sputnik      │  IP GATEWAY: 10.42.0.1          │  TRÁFICO: ⬇ 42 MB/s ⬆ 3.1M │
├────────────────────────────────────────────────────────────────────────────────────┤
│  [ ACCIONES RÁPIDAS ]                                                              │
│  [ Espacio ] Toggle Hotspot    [ B ] Modo Boost          [ G ] Modo Gaming (CAKE)  │
│  [ A ] AdBlock DNS (AdGuard)   [ S ] Modo Stealth        [ T ] Temporizador Clase  │
│  [ L ] Límite de Velocidad     [ D ] Selector DNS        [ P ] Test Velocidad      │
├───────────────────────────────────────────────────────┬────────────────────────────┤
│  DISPOSITIVOS CONECTADOS (2)                          │  MÉTRICAS & SESIÓN         │
│  > [IP]            [MAC]               [SEÑAL] [MBPS] │  • Duración: 1h 14m 20s    │
│  • 10.42.0.15     92:1c:df:28:fa:a7   -54 dBm   433  │  • Descargado: 4.82 GB     │
│  • 10.42.0.42     a4:b2:39:10:02:ee   -62 dBm   280  │  • Subido:     640 MB      │
│                                                       │  • IP Forwarding: Activo   │
│  [ K ] Expulsar/Bloquear seleccionado                 │  • Firewall: trusted       │
├───────────────────────────────────────────────────────┴────────────────────────────┤
│  [Q] Código QR   │  [Tab] Cambiar Panel   │  [F5] Refrescar   │  [Esc/Ctrl+C] Salir│
└────────────────────────────────────────────────────────────────────────────────────┘
```

### Controles del Teclado
| Tecla | Acción |
| :--- | :--- |
| `Espacio` | Encender / Apagar Zona Wi-Fi |
| `B` | Activar Modo Boost (Máxima Velocidad) |
| `G` | Alternar Modo Gaming (CAKE Anti-Bufferbloat) |
| `A` | Alternar Bloqueador de Anuncios (AdGuard DNS) |
| `S` | Alternar Modo Stealth (SSID Oculto) |
| `T` | Configurar Temporizador de Apagado |
| `L` | Configurar Límite de Velocidad (TBF Traffic Shaper) |
| `D` | Selector de Proveedor DNS y Benchmark |
| `P` | Test de Velocidad (Ping, Descarga y Subida) |
| `Q` | Mostrar Código QR para Celulares |
| `K` | Expulsar / Bloquear el dispositivo seleccionado |
| `↑` / `↓` | Seleccionar dispositivo en la lista |
| `Esc` / `q` | Salir del Dashboard |

---

## 💻 Comandos CLI (Automatización y Scripts)

Todos los controles pueden ejecutarse también directamente por línea de comandos:

```bash
# Control básico
thinkisp on                 # Enciende la zona Wi-Fi en 5GHz
thinkisp off                # Apaga y restaura la red anterior
thinkisp boost              # Activa modo turbo de máxima velocidad y latencia cero
thinkisp status             # Muestra estado y clientes conectados
thinkisp qr                 # Muestra código QR para conectar móviles

# Optimización & Calidad de Servicio
thinkisp gaming on/off      # Activa/desactiva colas CAKE anti-lag
thinkisp limit 30m          # Limita el hotspot a 30 Mbps
thinkisp limit reset        # Elimina el límite de velocidad
thinkisp adblock on/off     # Activa/desactiva bloqueo de anuncios
thinkisp dns benchmark      # Mide la latencia de todos los servidores DNS
thinkisp dns cloudflare     # Cambia a 1.1.1.1 (o google, quad9, adguard, auto)

# Seguridad & Control
thinkisp kick 10.42.0.15    # Expulsa y bloquea por IP o MAC
thinkisp unkick <MAC>       # Desbloquea un dispositivo
thinkisp blocked            # Lista de bloqueados
thinkisp stealth on/off     # Oculta o muestra el nombre de la red
thinkisp timer 50m          # Apagado programado en 50 minutos
thinkisp timer idle 10      # Apaga si pasan 10 min sin clientes

# Monitoreo
thinkisp top                # Monitor interactivo de tráfico en tiempo real
thinkisp speed              # Test de velocidad del enlace
thinkisp stats              # Estadísticas de datos de la sesión
thinkisp widget             # Emite JSON para Waybar / QuickShell
```

*(Nota: El comando corto `isp` funciona como alias idéntico a `thinkisp`).*

---

## 🛠️ Instalación / Installation

### Requisitos
* Linux (probado en Arch Linux, EndeavourOS, Fedora, Ubuntu, Debian).
* Tarjeta Wi-Fi con soporte de modo Access Point (`AP`).
* NetworkManager.
* Python 3.9+ (utiliza solo la biblioteca estándar, **cero dependencias externas requeridas**).

### Instalación Rápida
```bash
git clone https://github.com/Yzy-zu/ThinkISP.git
cd thinkisp
./install.sh
```

---

## 🧩 Compatibilidad y Shells

ThinkISP es 100% agnóstico y portable en cualquier distribución Linux (Ubuntu, Debian, Fedora, Arch Linux, Pop!_OS, openSUSE) y entorno gráfico (GNOME, KDE Plasma, XFCE, Hyprland, Sway, i3).

### Autocompletado de Shell (Bash, Zsh, Fish)
El instalador `./install.sh` instala automáticamente el autocompletado nativo:
- **Bash**: `~/.local/share/bash-completion/completions/thinkisp`
- **Zsh**: `~/.zsh/completion/_thinkisp` (recuerda tener `fpath=(~/.zsh/completion $fpath)` en tu `~/.zshrc`)
- **Fish**: `~/.config/fish/completions/thinkisp.fish`

### Salida JSON para Barras de Estado (`thinkisp widget`)
Si usas cualquier barra de estado (Polybar, Waybar, i3blocks, QuickShell), `thinkisp widget` emite JSON compatible con el estándar FreeDesktop.

---

## 🧪 Pruebas Automatizadas (Unit Tests)

ThinkISP incluye una suite completa de pruebas unitarias sin dependencias externas:

```bash
python3 tests/run_tests.py
```

---

## 📄 Licencia

Distribuido bajo la Licencia **MIT**. Consulta el archivo [`LICENSE`](LICENSE) para más detalles.
