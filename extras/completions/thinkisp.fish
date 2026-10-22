# ==============================================================================
# ThinkISP - Autocompletado nativo para Fish Shell
# ==============================================================================
# Para activar en tu sesión actual:
#   source ./extras/completions/thinkisp.fish
#
# Para instalación permanente:
#   mkdir -p ~/.config/fish/completions
#   cp ./extras/completions/thinkisp.fish ~/.config/fish/completions/thinkisp.fish
#   ln -sf ~/.config/fish/completions/thinkisp.fish ~/.config/fish/completions/isp.fish
# ==============================================================================

# Funciones de utilidad para clientes dinámicos y bloqueados
function __thinkisp_connected_clients
    if command -sq thinkisp
        thinkisp clients 2>/dev/null | string match -ra '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}|[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}'
    end
end

function __thinkisp_blocked_macs
    if command -sq thinkisp
        thinkisp blocked 2>/dev/null | string match -ra '([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}'
    end
end

for cmd in thinkisp isp
    # Deshabilitar autocompletado de archivos estándar del sistema
    complete -c $cmd -f

    # Opciones Globales
    complete -c $cmd -s h -l help -d "Muestra ayuda y opciones disponibles"

    # Subcomandos Principales
    complete -c $cmd -n "__fish_use_subcommand" -a "setup config" -d "Panel técnico según el equipo"
    complete -c $cmd -n "__fish_use_subcommand" -a "on" -d "Configura si es necesario y activa el punto de acceso"
    complete -c $cmd -n "__fish_use_subcommand" -a "off" -d "Desactiva la zona Wi-Fi y restaura conexión previa"
    complete -c $cmd -n "__fish_use_subcommand" -a "boost" -d "Configura CAKE y ahorro de energía Wi-Fi"
    complete -c $cmd -n "__fish_use_subcommand" -a "status" -d "Muestra el estado y dispositivos conectados"
    complete -c $cmd -n "__fish_use_subcommand" -a "clients" -d "Lista dispositivos conectados con su señal y velocidad"
    complete -c $cmd -n "__fish_use_subcommand" -a "speed" -d "Test de velocidad real (Ping, Descarga y Subida)"
    complete -c $cmd -n "__fish_use_subcommand" -a "top" -d "Monitor interactivo de tráfico en vivo por dispositivo"
    complete -c $cmd -n "__fish_use_subcommand" -a "stats" -d "Contador de datos transferidos en la sesión"
    complete -c $cmd -n "__fish_use_subcommand" -a "widget" -d "Salida JSON para Waybar / QuickShell"
    complete -c $cmd -n "__fish_use_subcommand" -a "kick" -d "Bloqueo de clientes (nftables)"
    complete -c $cmd -n "__fish_use_subcommand" -a "unkick" -d "Desbloquea una dirección MAC bloqueada"
    complete -c $cmd -n "__fish_use_subcommand" -a "blocked" -d "Lista las direcciones MAC bloqueadas"
    complete -c $cmd -n "__fish_use_subcommand" -a "stealth" -d "Modo invisible (ocultar/mostrar SSID)"
    complete -c $cmd -n "__fish_use_subcommand" -a "timer" -d "Temporizador de apagado por tiempo o inactividad"
    complete -c $cmd -n "__fish_use_subcommand" -a "gaming" -d "Modo anti-lag para juegos y VoIP (Colas CAKE)"
    complete -c $cmd -n "__fish_use_subcommand" -a "limit" -d "Limita el ancho de banda del hotspot"
    complete -c $cmd -n "__fish_use_subcommand" -a "adblock" -d "Bloqueador de anuncios a nivel de red DNS"
    complete -c $cmd -n "__fish_use_subcommand" -a "dns" -d "Selector y benchmark de servidores DNS de ultra baja latencia"
    complete -c $cmd -n "__fish_use_subcommand" -a "qr" -d "Muestra el código QR para conectar móviles"
    complete -c $cmd -n "__fish_use_subcommand" -a "band" -d "Cambia frecuencia entre 5GHz y 2.4GHz"
    complete -c $cmd -n "__fish_use_subcommand" -a "toggle" -d "Alterna entre encendido y apagado"
    complete -c $cmd -n "__fish_use_subcommand" -a "tui" -d "Abre el Dashboard Interactivo en terminal"

    # Opciones de Subcomandos

    # widget
    complete -c $cmd -n "__fish_seen_subcommand_from widget" -s w -l watch -d "Emitir actualizaciones continuas cada 2s"

    # stealth
    complete -c $cmd -n "__fish_seen_subcommand_from stealth" -a "on" -d "Ocultar el SSID (modo invisible)"
    complete -c $cmd -n "__fish_seen_subcommand_from stealth" -a "off" -d "Mostrar el SSID (modo visible)"
    complete -c $cmd -n "__fish_seen_subcommand_from stealth" -a "status" -d "Ver estado actual del modo stealth"

    # gaming
    complete -c $cmd -n "__fish_seen_subcommand_from gaming" -a "on" -d "Activar colas CAKE anti-bufferbloat"
    complete -c $cmd -n "__fish_seen_subcommand_from gaming" -a "off" -d "Desactivar modo gaming"
    complete -c $cmd -n "__fish_seen_subcommand_from gaming" -a "status" -d "Ver estado de colas CAKE"
    complete -c $cmd -n "__fish_seen_subcommand_from gaming" -a "toggle" -d "Alternar modo gaming"

    # limit
    complete -c $cmd -n "__fish_seen_subcommand_from limit" -a "reset" -d "Eliminar límites de velocidad"
    complete -c $cmd -n "__fish_seen_subcommand_from limit" -a "10m" -d "Limitar a 10 Mbps"
    complete -c $cmd -n "__fish_seen_subcommand_from limit" -a "25m" -d "Limitar a 25 Mbps"
    complete -c $cmd -n "__fish_seen_subcommand_from limit" -a "50m" -d "Limitar a 50 Mbps"
    complete -c $cmd -n "__fish_seen_subcommand_from limit" -a "100m" -d "Limitar a 100 Mbps"

    # adblock
    complete -c $cmd -n "__fish_seen_subcommand_from adblock" -a "on" -d "Activar bloqueo de anuncios por DNS"
    complete -c $cmd -n "__fish_seen_subcommand_from adblock" -a "off" -d "Desactivar bloqueo de anuncios"
    complete -c $cmd -n "__fish_seen_subcommand_from adblock" -a "status" -d "Ver estado del bloqueador"
    complete -c $cmd -n "__fish_seen_subcommand_from adblock" -a "toggle" -d "Alternar bloqueo de anuncios"

    # dns
    complete -c $cmd -n "__fish_seen_subcommand_from dns" -a "cloudflare" -d "1.1.1.1 / 1.0.0.1 (Baja latencia)"
    complete -c $cmd -n "__fish_seen_subcommand_from dns" -a "google" -d "8.8.8.8 / 8.8.4.4 (Ultra estable)"
    complete -c $cmd -n "__fish_seen_subcommand_from dns" -a "quad9" -d "9.9.9.9 (Seguridad y filtrado malware)"
    complete -c $cmd -n "__fish_seen_subcommand_from dns" -a "adguard" -d "94.140.14.14 (Filtrado de publicidad)"
    complete -c $cmd -n "__fish_seen_subcommand_from dns" -a "default" -d "Restaurar configuración DNS anterior"
    complete -c $cmd -n "__fish_seen_subcommand_from dns" -a "auto" -d "Restaurar configuración DNS anterior"
    complete -c $cmd -n "__fish_seen_subcommand_from dns" -a "benchmark" -d "Medir latencia de todos los servidores DNS"

    # band
    complete -c $cmd -n "__fish_seen_subcommand_from band" -a "5g" -d "5 GHz si está disponible en este equipo"
    complete -c $cmd -n "__fish_seen_subcommand_from band" -a "5" -d "5 GHz si está disponible"
    complete -c $cmd -n "__fish_seen_subcommand_from band" -a "2.4g" -d "2.4 GHz Ch 6 (Compatibilidad y mayor alcance)"
    complete -c $cmd -n "__fish_seen_subcommand_from band" -a "2.4" -d "2.4 GHz Ch 6"
    complete -c $cmd -n "__fish_seen_subcommand_from band" -a "auto" -d "Selección automática de banda"

    # timer
    complete -c $cmd -n "__fish_seen_subcommand_from timer" -a "cancel" -d "Cancelar temporizador activo"
    complete -c $cmd -n "__fish_seen_subcommand_from timer" -a "idle" -d "Apagar automáticamente si no hay clientes"
    complete -c $cmd -n "__fish_seen_subcommand_from timer" -a "status" -d "Consultar tiempo restante del temporizador"
    complete -c $cmd -n "__fish_seen_subcommand_from timer" -a "15m" -d "Apagar automáticamente en 15 minutos"
    complete -c $cmd -n "__fish_seen_subcommand_from timer" -a "30m" -d "Apagar automáticamente en 30 minutos"
    complete -c $cmd -n "__fish_seen_subcommand_from timer" -a "45m" -d "Apagar automáticamente en 45 minutos"
    complete -c $cmd -n "__fish_seen_subcommand_from timer" -a "1h" -d "Apagar automáticamente en 1 hora"
    complete -c $cmd -n "__fish_seen_subcommand_from timer" -a "2h" -d "Apagar automáticamente en 2 horas"

    complete -c $cmd -n "__fish_use_subcommand" -a "recover" -d "Reintenta restaurar la red con el hotspot apagado"
    complete -c $cmd -n "__fish_seen_subcommand_from dns" -a "restore" -d "Restaurar configuración DNS anterior"

    # kick
    complete -c $cmd -n "__fish_seen_subcommand_from kick" -a "(__thinkisp_connected_clients)" -d "Dispositivo conectado"

    # unkick
    complete -c $cmd -n "__fish_seen_subcommand_from unkick" -a "(__thinkisp_blocked_macs)" -d "Dirección MAC bloqueada"
end

for cmd in thinkisp isp
    complete -c $cmd -n "__fish_use_subcommand" -a "doctor" -d "Diagnóstico de compatibilidad sin modificar la red"
    complete -c $cmd -n "__fish_seen_subcommand_from doctor" -l json -d "Informe JSON"
end

for cmd in thinkisp isp
    complete -c $cmd -n "__fish_use_subcommand" -a network -d "Plan de conectividad por perfil (sin aplicar)"
    complete -c $cmd -n "__fish_seen_subcommand_from network" -a "inspect plan"
    complete -c $cmd -n "__fish_seen_subcommand_from network" -l json
    complete -c $cmd -n "__fish_seen_subcommand_from network; and __fish_seen_subcommand_from plan" -l ap -r
    complete -c $cmd -n "__fish_seen_subcommand_from network; and __fish_seen_subcommand_from plan" -l upstream -r
    complete -c $cmd -n "__fish_seen_subcommand_from network; and __fish_seen_subcommand_from plan" -l mode -r -a "auto routed repeater"
    complete -c $cmd -n "__fish_seen_subcommand_from network; and __fish_seen_subcommand_from plan" -l engine -r -a "auto none opennds"
    complete -c $cmd -n "__fish_seen_subcommand_from network; and __fish_seen_subcommand_from plan" -l save
end
