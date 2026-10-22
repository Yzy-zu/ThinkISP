# thinkISP

Gestor de puntos de acceso para Linux. Permite configurar una red doméstica con las capacidades que el adaptador Wi-Fi y NetworkManager exponen en cada equipo.

El programa detecta las interfaces del sistema y consulta al controlador sus canales, restricciones regulatorias y límites de potencia. No presupone una marca de computadora, un SSID, una contraseña, un canal ni una velocidad de enlace.

## Instalación

Requiere Linux, Python 3.9 o posterior y NetworkManager 1.40 o posterior con soporte de `nmcli --offline`, `iw` e `ip`. Para compartir la conexión, NetworkManager necesita su backend DHCP/DNS disponible; según la distribución puede requerir instalar `dnsmasq`. El adaptador debe anunciar soporte para modo AP.

```bash
git clone https://github.com/Yzy-zu/ThinkISP.git thinkisp
cd thinkisp
./install.sh
thinkisp
```

El instalador crea lanzadores de usuario, una entrada de escritorio y autocompletado para Bash, Zsh y Fish. Respeta XDG_DATA_HOME y XDG_CONFIG_HOME; los destinos personalizados deben ser rutas absolutas. Instala las herramientas del sistema con el gestor de paquetes de tu distribución antes de ejecutarlo. Conserva el checkout: los lanzadores lo ejecutan directamente con el intérprete detectado durante la instalación.

También puedes trabajar directamente desde el checkout:

```bash
python3 main.py
```

Herramientas adicionales por función: `tc` para verificar QoS, `qrencode` para códigos QR, `dig` para el benchmark DNS, `curl` para medir transferencia y `ethtool` para consultar la velocidad de la interfaz de salida. Las notificaciones usan `notify-send` cuando está disponible. El código Python utiliza la biblioteca estándar.

## Compatibilidad y diagnóstico

Puedes consultar requisitos, permisos, radio, canales y propiedades disponibles sin activar la red:

```bash
python3 main.py doctor
python3 main.py doctor --json
```

El informe distingue herramientas obligatorias, funciones opcionales y comprobaciones pendientes. No modifica perfiles, DNS, firewall o regulación, no solicita autorización y no consulta contraseñas. Una interfaz o un parser disponible no certifican una conexión Wi-Fi real. Consulta el [informe de compatibilidad y pruebas ejecutadas](docs/compatibility.md).

## Primera ejecución y configuración

Si no existe un perfil de thinkISP, `thinkisp`, `thinkisp tui` y `thinkisp on` abren el asistente antes de encender la red. El asistente solicita el SSID y la contraseña elegidos por el usuario; el nombre del equipo se usa únicamente como sugerencia inicial para el SSID.

El panel permite revisar y editar:

- SSID, contraseña y seguridad personal aceptada por el backend. Si la tarjeta no informa su soporte para WPA3, el panel lo señala como sin confirmar y la activación puede ser rechazada.
- Banda, canal y ancho de canal, según las capacidades del controlador y de NetworkManager.
- Límite de potencia de transmisión, respetando los límites anunciados para los canales seleccionados.
- Red visible u oculta, ahorro de energía y MTU.
- Aislamiento entre clientes y protección de tramas de gestión cuando NetworkManager los expone.
- Dirección/subred IPv4 y opciones de DHCP disponibles en la versión instalada.
- Propiedades técnicas adicionales de Wi-Fi, seguridad, IPv4, IPv6 y control de tráfico mediante el editor avanzado.

La vista de capacidades muestra la información completa de la radio y las restricciones regulatorias del sistema. El editor avanzado obtiene su lista de propiedades del `nmcli` instalado; una propiedad aceptada por el parser puede todavía ser rechazada por el controlador al aplicar la configuración. Ese rechazo se informa como error.

Para volver al panel:

```bash
thinkisp setup
# Alias:
thinkisp config
```

En el panel, presiona **C** o selecciona **Configurar**. El editor se abre en la misma terminal y, al terminar, vuelve al panel.

Seleccionar **Guardar** valida los ajustes antes de escribirlos en NetworkManager. Si el hotspot está activo, intenta aplicar los cambios reiniciándolo. **Cancelar** antes de guardar no crea ni modifica un perfil.

La selección automática de banda y canal queda a cargo de NetworkManager. Un ancho de canal fijo requiere seleccionar banda y canal. No se ofrecen canales marcados como deshabilitados o sin autorización para iniciar transmisión (`no IR`). El programa consulta el dominio regulatorio del sistema y no fuerza un país. Los canales DFS dependen del soporte de detección de radar del controlador y pueden requerir espera o ser rechazados.

La potencia personalizada se aplica con `iw` como límite en dBm. Requiere los permisos que determine el sistema. Si falla al arrancar, el programa intenta apagar el AP y restaurar la Wi-Fi anterior; si esa recuperación también falla, informa ambos errores. La opción automática inicial no ejecuta cambios privilegiados de potencia.

Las credenciales se guardan en NetworkManager. El JSON de estado de thinkISP no almacena la contraseña. Una ejecución sin terminal no inventa credenciales ni crea una red: requiere completar primero `thinkisp setup`.

## Perfiles e interfaces

El perfil predeterminado lleva el nombre del proyecto. Si existe, conserva sus ajustes. Un perfil con el mismo nombre que no sea un AP se rechaza en lugar de sobrescribirlo.

Variables opcionales:

| Variable | Uso |
| --- | --- |
| `THINKISP_PROFILE` | Nombre del perfil que se va a configurar y gestionar. |
| `THINKISP_WLAN` | Interfaz Wi-Fi elegida por el usuario cuando hay varios adaptadores. |
| `THINKISP_ETH` | Interfaz de salida elegida por el usuario. |
| `THINKISP_SSID` | Sugerencia inicial de SSID en el asistente; no reemplaza un perfil existente. |
| `XDG_STATE_HOME` | Directorio base de los archivos de estado del usuario. |

La detección prefiere el adaptador vinculado al perfil existente; para un perfil nuevo, prefiere un adaptador que anuncie modo AP. No sustituye una interfaz ausente por nombres supuestos. Si cambias de adaptador, vuelve a ejecutar el proceso y revisa el panel.

## Comandos

```bash
thinkisp on
thinkisp off
thinkisp status
thinkisp clients
thinkisp qr
thinkisp widget
thinkisp widget --watch
thinkisp top
thinkisp stats
thinkisp timer 45m
thinkisp timer idle 10
thinkisp timer cancel
thinkisp stealth on
thinkisp stealth off
thinkisp band auto
thinkisp gaming on
thinkisp gaming off
thinkisp limit 30m
thinkisp limit reset
thinkisp boost
thinkisp dns benchmark
thinkisp dns cloudflare
thinkisp adblock on
thinkisp adblock off
thinkisp speed
```

`isp` es un alias de `thinkisp`. Consulta `thinkisp --help` para los argumentos disponibles.

El estado, el widget y el QR consultan el perfil y la interfaz del equipo. Si no se puede obtener un dato de radio, se muestra «Sin datos». Las consultas no muestran la contraseña salvo en la función explícita de conexión/QR.

Los fallos operativos se imprimen en stderr y el CLI termina con código 1. Una cancelación interactiva termina con código 130. Las operaciones requeridas comprueban el resultado del comando antes de anunciar éxito o guardar un estado de éxito.

## Panel de terminal

Abre `thinkisp tui` (o `python3 main.py tui` desde el proyecto). Necesita al menos **76 columnas × 24 filas**; una ventana más amplia muestra más dispositivos y columnas de enlace.

La pantalla separa conexión, tráfico de la sesión, acciones y dispositivos. Los nombres de red, interfaces, radio y equipos se consultan al sistema. **Tab** cambia de panel; las **flechas** mueven la selección y **Enter** abre una acción o los detalles del dispositivo. **RePág/AvPág** e **Inicio/Fin** recorren listas largas. **?** muestra los atajos. El ratón permite seleccionar acciones, opciones y botones de los diálogos.

Las consultas se ejecutan en segundo plano con un intervalo de dos segundos entre lecturas. El teclado se atiende cada 100 ms mientras no haya una interacción externa del sistema. Si una lectura falla, se conserva la última muestra y se marca como sin actualizar. Los resultados anteriores a un cambio de configuración se descartan. Durante las mediciones DNS y de transferencia puedes cerrar el diálogo y seguir navegando; la telemetría continúa.

Los cambios de red se procesan uno a la vez y muestran su progreso. **Q** cierra el panel conservando el estado del hotspot; si se está aplicando un cambio, espera a que termine antes de salir. **O** apaga o enciende la red. **C** abre el editor técnico interactivo en la misma terminal.

**R** consulta las credenciales y genera el QR una vez por apertura. Si no cabe completo, solicita ampliar la ventana y conserva los datos para conectarse manualmente. En los detalles de un dispositivo puedes bloquear o permitir su acceso; la pantalla distingue la configuración de bloqueo de la asociación Wi-Fi.

## Alcance actual

- CAKE y el límite TBF ocupan la cola raíz: configurar uno sustituye al otro. Con el AP activo, se consulta `tc` para comprobar la cola antes de anunciar el cambio. Con el AP apagado, se indica que la configuración queda para el siguiente encendido.
- Boost configura CAKE y desactiva el ahorro de energía Wi-Fi. Conserva banda, canal y potencia; no cambia el perfil de CPU del equipo ni garantiza una latencia o velocidad determinadas.
- DNS aplica cambios temporales al perfil activo de la salida detectada, identificado por UUID. Guarda antes los DNS IPv4/IPv6 y sus opciones automáticas; al apagar restaura esa configuración, incluidos DNS manuales. `thinkisp dns restore` (también `auto` o `default`) restaura la configuración anterior. Apagar AdBlock recupera el proveedor que habías seleccionado antes de activarlo. Con el AP apagado, las preferencias se aplican en el siguiente encendido.
- El monitor muestra transferencia global. La transferencia individual por cliente está pendiente y aparece como «No medido».
- `kick IP_o_MAC` bloquea el acceso del cliente al equipo/router e Internet mediante nftables en la interfaz del AP; `unkick MAC` lo permite de nuevo y `blocked` lista las preferencias guardadas. Los bloqueos se aplican antes de abrir el AP y se retiran al apagarlo, conservando la lista para la siguiente sesión. El cliente puede seguir asociado al Wi-Fi; el aislamiento entre clientes se configura aparte según lo que soporte el adaptador.
- El control de acceso necesita `nft` y permisos de administración de red. Desde una sesión interactiva puede solicitar autorización con `pkexec`; el monitor en segundo plano no abre solicitudes de autorización. Las reglas se limitan a una tabla propia por usuario y perfil; no se vacía el firewall del sistema.
- Los cambios DNS y de acceso llevan un registro de recuperación. Si una restauración falla, se informa y se conserva el registro para reintentar con `thinkisp recover`, con el AP apagado. Si otro programa cambió el DNS durante la sesión, thinkISP informa el conflicto y conserva los datos anteriores sin sobrescribir ese cambio.
- La compatibilidad de una configuración de radio debe confirmarse con el adaptador y el sistema de destino. No se garantiza soporte de AP únicamente por tener Wi-Fi, ni se promete un ancho o tasa de transferencia fija.

## Pruebas

```bash
python3 tests/run_tests.py
```

Incluyen errores de permisos y activación, datos dinámicos de interfaz, validación de radio, creación y conservación de perfiles, cancelación del asistente, manejo de errores en CLI/TUI y comprobaciones offline de NetworkManager cuando `nmcli` está instalado. Las pruebas no activan ni reconfiguran la red real.

Para comprobar teclado, diálogos, resultados de fondo y redimensionamiento con curses real en un pseudoterminal, usando datos de prueba sin modificar la red:

```bash
python3 tests/integration/tui_roundtrip.py
```

La prueba de integración del firewall requiere Linux, `nft`, `ip`, `ping` y soporte de namespaces de usuario/red. Comprueba bloqueo y desbloqueo IPv4/IPv6 y conservación de tablas ajenas en redes virtuales aisladas:

```bash
unshare --user --map-root-user --net python3 tests/integration/nft_roundtrip.py
```

Las comprobaciones con mocks, redes virtuales y perfiles offline no sustituyen una prueba de conexión, DHCP, navegación y apagado con un cliente Wi-Fi real.

## Licencia

[MIT](LICENSE).
