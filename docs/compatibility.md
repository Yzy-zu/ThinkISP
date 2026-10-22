# Compatibilidad de thinkISP

Revisión del 22 de octubre de 2026. Las pruebas automatizadas no activaron un hotspot ni cambiaron perfiles, DNS, reglas del equipo o el dominio regulatorio. El intento de AP físico posterior y su fallo de firmware se describen en el diagnóstico del equipo y en la investigación de 5 GHz.

## Requisitos por función

| Función | Requisitos y límites |
| --- | --- |
| Plataforma | Linux. Windows y macOS se rechazan con un mensaje operativo. No se depende de una marca de PC ni de una arquitectura de CPU concreta. |
| Python | 3.9 o posterior. Se ejecutó la suite con CPython 3.9.25, 3.10.22, 3.11.17, 3.12.15, 3.13.16 y 3.14.8 en Linux x86_64. |
| Perfil y asistente | NetworkManager y `nmcli` con `--offline`, disponible desde 1.40; `iw` e `ip`. El asistente consulta las propiedades aceptadas por el parser instalado. |
| Punto de acceso | Adaptador gestionado por NetworkManager que anuncie AP y canales autorizados para iniciar transmisión. DHCP/DNS compartido necesita el backend de la distribución, habitualmente dnsmasq. |
| Banda, canal y ancho | Intersección de las capacidades de cada banda, restricciones de canal y opciones aceptadas por nmcli. Se reconocen frecuencias enteras y decimales de iw. DFS, firmware y soporte concreto de AP requieren prueba de activación. |
| 6 GHz | Sólo se ofrece si la radio y nmcli admiten la banda. Se exige SAE/WPA3 y no se permite desactivar PMF. No se certificó una activación en 6 GHz. |
| Potencia | Los límites se leen por canal, incluidos valores negativos en dBm. Aplicar cambios con iw necesita los permisos del sistema. |
| Compartir Internet y DNS | Salida distinta de la interfaz del AP. Se respeta la métrica de las rutas y se admiten nombres personalizados, USB, bridges y bonds. IPv6 se usa como alternativa de detección; una salida exclusivamente IPv6 no garantiza servicio IPv4 a los clientes. VPN y reglas de encaminamiento específicas requieren validación propia. |
| Bloqueo por MAC | `nft`, JSON y soporte del kernel para tabla netdev y hook ingress. Requiere CAP_NET_ADMIN o autorización PolicyKit; `pkexec` no es obligatorio para el AP básico. |
| CAKE/TBF | Soporte del kernel y de NetworkManager para la cola elegida. `tc` comprueba la cola aplicada; una aceptación offline no confirma que el módulo del kernel esté disponible. |
| Plan de conectividad | Inventario y preferencias por UUID; modos routed/repeater y motor previsto. La fase 2 aplica el plan al encender, con AP virtual si hace falta, rutas propias y recuperación. La concurrencia anunciada no certifica activación real; consulta [límites de fase 2](portal-roadmap.md). |
| Portal integrado | nftables, iproute2, Linux con sockets Unix y credenciales, `fork`, y root o `pkexec` con agente PolicyKit. Requiere plan por perfil y conexión automática del AP desactivada. HTTP sin privilegios; sin interceptar HTTPS. Las plantillas y duraciones configurables siguen pendientes. |
| Panel | Módulo curses de Python, entrada/salida TTY, locale UTF-8 y TERM válido. Tamaño mínimo 76×24. La selección también se distingue sin color. Sin TTY se usan comandos CLI. |
| Diagnósticos y extras | `dig` para comparar DNS, `curl` para transferencia, `qrencode` para QR, `ethtool` para velocidad del enlace y `notify-send` para notificaciones. Su ausencia sólo afecta a la función correspondiente. |

El requisito `--offline` está documentado por el [anuncio oficial de NetworkManager 1.40](https://networkmanager.dev/blog/networkmanager-1-40/). Las opciones dependen de la versión instalada: [propiedades Wi-Fi](https://www.networkmanager.dev/docs/api/latest/settings-802-11-wireless.html) y [manual de nmcli](https://networkmanager.pages.freedesktop.org/NetworkManager/NetworkManager/nmcli.html).

## Comprobaciones ejecutadas

| Comprobación | Resultado |
| --- | --- |
| Suite en seis versiones reales de Python, 3.9–3.14 | 401 pruebas aprobadas por versión, incluido el portal de fase 3. |
| Curses real en pseudoterminal, en las seis versiones | Color real de curses y paleta de acciones con búsqueda, cinco vistas con teclas de función, configuración integrada, temporizador, límites, bloqueados, DNS, resultado tardío de un trabajo, redimensionamiento a 80×24 y salida limpia aprobados. Datos de red simulados. |
| Formulario de configuración curses real, Python 3.9–3.14 | Edición de SSID Unicode, contraseña oculta y confirmada, selección con flechas de banda/canal, pestañas, F2 y redimensionamiento a 80×24 aprobados. Guardado simulado, sin modificar la red. |
| Parser offline instalado: nmcli 1.58.1 | Formulario completo con canal automático en banda automática/2.4/5 GHz, reinicio de canal fijo a automático, SSID Unicode, seguridad, visibilidad, ahorro de energía, MTU, PMF, aislamiento, DHCP, DNS en perfil de salida, CAKE/TBF y propiedades opcionales aprobados. Ningún perfil real creado. |
| Rutas de fase 2 en kernel Linux aislado | Salida elegida, bloqueo de fallback al eliminarla, limpieza propia y otra WAN conservada. Interfaces ficticias; no se tocó la red del equipo. |
| Portal en kernel y supervisor aislados | HTTP sin root, redirección, token de un uso, autorización y revocación, corte de TCP establecido, IPv6 bloqueado, vencimiento sin refresco, caída de HTTP y recuperación conservando tablas ajenas. Sin activar radios ni DHCP reales. |
| Wheel, Python 3.14 | Construcción e instalación en venv temporal; módulos y entry points `thinkisp` e `isp` verificados sin descargar dependencias. |
| Instalador | Rutas con espacios, comillas, `$`, `&`, `|` y `%`; lanzadores y completados en directorios de usuario temporales. Desktop Entry validado. |
| Shell | Sintaxis de Bash y Fish verificada localmente. Zsh queda incluido en la matriz de CI. |
| Distribución fuente | Tarball generado fuera del árbol de origen, checksum y ejecución de ayuda desde el contenido extraído. No se incluye el archivo dentro de sí mismo. |

La CI queda configurada para Python 3.9–3.14 y parsers de NetworkManager de Debian bookworm/trixie y Ubuntu 24.04. **Configurar esa matriz no equivale a haberla ejecutado en GitHub en esta sesión**. Tampoco equivale a certificar el Wi-Fi real de esas distribuciones.

## Regresión de canal automático y cobertura del guardado

El formulario conservaba `0` para representar canal automático y se enviaba literalmente a nmcli, que rechaza ese valor al modificar la propiedad. Se corrigió la serialización compartida: `0`/vacío en el formulario se convierte en el argumento vacío que restablece el canal. El comando de cambio de banda utiliza esa misma conversión.

Las pruebas anteriores del parser usaban un canal fijo y no cubrían el formulario inicial completo. `tests/test_profile_nmcli.py` recorre ahora el guardado de producción usando el parser nmcli real y sólo intercepta las escrituras al daemon y al estado local. Comprueba creación, edición, restablecimiento de canal, controles opcionales y valores inválidos. También valida el comando de banda. Se comprueba que los argumentos validados y enviados al guardado sean idénticos y que un rechazo no intente escribir perfiles.

El parser instalado acepta algunos valores numéricos fuera del rango de las enumeraciones, por ejemplo `powersave=99`. Por eso ahorro de energía, PMF, aislamiento y visibilidad tienen además validación local. Estas comprobaciones cubren el perfil y sus argumentos; no certifican aceptación del firmware, permisos, asociación o navegación.

## Diagnóstico del equipo consultado

La consulta de sólo lectura encontró una interfaz con controlador `iwlwifi`, AP anunciado en 2.4 GHz, 13 canales permitidos y anchos aceptados de 20/40 MHz. Los canales de 5 GHz estaban restringidos para iniciar transmisión. Son observaciones del equipo y de su estado regulatorio durante esta revisión, no valores usados por el programa ni una limitación global de thinkISP.

La [investigación específica de 5 GHz](5ghz-investigation.md) identifica la Intel 8265, confirma su conexión cliente de 5 GHz y explica la restricción LAR del stack instalado y la prueba temporal de un módulo modificado. Tras cargarlo, el diagnóstico real detectó ambas bandas y nueve canales de 5 GHz sin `no IR` ni DFS; **el intento real de AP de 5 GHz falló con un assert del firmware y error al configurar beacons**. El perfil temporal se eliminó y la conexión cliente se restauró automáticamente. El módulo original permanece intacto; la reversión del módulo en memoria requiere autenticación local o reinicio.

El diagnóstico no encontró una salida distinta de la interfaz del AP y la lectura directa de nftables requería más permisos. Ambas condiciones se informan sin modificar la red ni solicitar autorización desde el diagnóstico.

La salida de Internet se vuelve a detectar durante el uso. La selección del adaptador Wi-Fi se realiza al iniciar el proceso: si se sustituye por otro adaptador con diferente nombre, hay que volver a abrir el panel o seleccionar THINKISP_WLAN antes de ejecutarlo.

No se comprobaron asociación con clientes, concesiones DHCP, navegación, rendimiento, AP de 6 GHz, múltiples radios simultáneas ni arquitecturas ARM/aarch64. Se intentó activar un AP de 5 GHz con el módulo experimental y falló; las capacidades anunciadas por sí solas no certifican el funcionamiento real.

## Diagnóstico reproducible

```bash
python3 main.py doctor
python3 main.py doctor --json
python3 -m unittest discover -s tests
python3 tests/integration/nmcli_offline.py
python3 tests/integration/tui_roundtrip.py
python3 tests/integration/setup_roundtrip.py
```

`doctor` devuelve 1 si hay un requisito básico ausente, rechazado o sin confirmar. Las herramientas opcionales y la falta de TTY se notifican como avisos. La salida JSON mantiene `activation_tested: false` porque el diagnóstico no activa la radio. No consulta contraseñas ni escribe estado.

Para comprobar el wheel sin red, instala antes las herramientas de desarrollo declaradas en `pyproject.toml` (setuptools y wheel) y ejecuta:

```bash
python3 tests/integration/package_roundtrip.py
```

Las pruebas de nftables con tráfico IPv4/IPv6 en redes virtuales tienen su procedimiento en el README; no forman parte del diagnóstico sobre las interfaces reales.

El portal de fase 3 también se recorre en la prueba de pseudoterminal. Su apertura automática en cada sistema cliente, DHCP y navegación mediante un AP físico siguen pendientes. Los comandos de integración aislada y sus requisitos se describen en [la hoja de ruta](portal-roadmap.md#validación-reproducible).
