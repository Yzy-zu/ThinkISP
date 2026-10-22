# Portal cautivo · thinkISP 0.1 beta

El proyecto es general: no se condiciona a un modelo de PC, interfaz, país ni distribución concreta. Las capacidades anunciadas no certifican la activación real.

| Fase | Entrega | Estado |
| --- | --- | --- |
| 1 | Inventario, separación AP/entrada/motor y plan por perfil | Implementada; guarda intención sin aplicarla |
| 2 | Conectividad Ethernet/USB/otra radio y repetidor de una radio cuando sea compatible | Pendiente |
| 3 | Motor de portal: acceso restringido, página, autorización y revocación | Pendiente |
| 4 | Sesiones: minutos, horas, días, sin vencimiento y políticas de reconexión | Pendiente |
| 5 | Plantillas de términos, registro y anuncio; condiciones configurables | Pendiente |
| 6 | Importación HTML/CSS/JS, recursos locales, API y vista previa | Pendiente |
| 7 | Administración completa del portal en TUI y CLI | Pendiente |
| 8 | Validación física en varios sistemas, recuperación y distribución | Pendiente |

## Qué hace fase 1

`thinkisp network inspect` consulta NetworkManager, iproute2, iw y el inventario de tablas nftables. No escanea redes, no crea interfaces, no cambia DHCP/DNS y no instala ni inicia motores de portal. Cuando faltan permisos o herramientas lo informa sin inventar soporte. Consultar un binario instalado no certifica su integración.

El inventario describe interfaces, su transporte USB cuando sysfs permite identificarlo, radios físicas, modos, canales AP utilizables, combinaciones de interfaces, rutas predeterminadas IPv4/IPv6 y dependencias. No incluye SSID, contraseñas ni el contenido completo de las reglas del firewall.

El plan separa `ap_interface`, `upstream_interface`, `mode` y `portal_engine`. Las selecciones automáticas se vuelven a resolver contra el inventario actual. La entrada se elige por las rutas observadas, prefiriendo IPv4 y menor métrica. Una interfaz conectada sin ruta no se anuncia como Internet comprobado. VPN, bridges y USB se inventarían como interfaces del sistema, sin nombres supuestos.

`routed` describe interfaces y radios separadas. `repeater` describe una radio compartida, incluyendo dos interfaces virtuales sobre el mismo PHY. Se analizan los presupuestos de los grupos `#{ managed }`, `#{ AP }`, el total y el número de canales. P2P-GO no sustituye AP. Una lista de modos por sí sola no confirma concurrencia. Si todas las combinaciones válidas exigen un canal, se informa esa restricción.

El informe diferencia incompatibilidades conocidas y datos sin verificar. También avisa de reglas nftables existentes y dependencias del motor. La convivencia efectiva con otros firewalls, los conflictos de puertos/subredes y la aceptación del firmware deben verificarse al implementar la ejecución de fases 2/3.

## Uso

```bash
thinkisp network inspect
thinkisp network inspect --json
thinkisp network plan --mode auto --engine auto
thinkisp network plan --ap INTERFAZ_AP --upstream INTERFAZ_ENTRADA --mode routed --engine opennds
```

Para guardar el último ejemplo, añade `--save`. `INTERFAZ_AP` e `INTERFAZ_ENTRADA` son marcadores: usa nombres del inventario de esa instalación. Para devolver una selección de interfaz a detección automática, usa `--ap auto` o `--upstream auto`.

El editor interactivo está en **F3 Red → Conectividad**, acceso rápido **N**, y en la paleta `/` buscando conectividad. Permite elegir interfaces, topología y motor, consultar restricciones y guardar el plan. No cambia la red activa.

Guardar requiere un perfil AP ya creado con `thinkisp setup`. La intención se almacena por UUID en `network_profiles` dentro del estado privado existente, con versión de esquema. No almacena contraseñas. No se modifican conexiones de NetworkManager ni se aplica automáticamente el plan guardado al arrancar el hotspot actual. `applied=false` y `portal_enabled=false` son explícitos.

El motor previsto puede ser `none`, `opennds` o `auto`. openNDS se detecta como candidato opcional; su adaptación operativa es fase 3. El motor no se descarga, habilita ni ejecuta en fase 1.

## Validación

Las pruebas usan instalaciones simuladas independientes para enlaces cableados/USB, distintas radios, una radio con/sin concurrencia, presupuestos incompatibles, varios canales, selección de rutas, errores de permisos y configuraciones dañadas. Se verifica aislamiento entre perfiles, rechazo de planes incompatibles y ausencia de comandos de mutación durante el inventario. También se comprueban CLI, editor, curses y empaquetado.

El diagnóstico del equipo de desarrollo sólo es una observación adicional; no define las capacidades generales del proyecto. La aprobación de fase 1 no equivale a tener un portal cautivo ni un repetidor operativos.
