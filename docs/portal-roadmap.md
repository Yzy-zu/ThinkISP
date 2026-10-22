# Portal cautivo · thinkISP 0.1 beta

El proyecto es general: no se condiciona a un modelo de PC, interfaz, país ni distribución concreta. Las capacidades anunciadas no certifican la activación real.

| Fase | Entrega | Estado |
| --- | --- | --- |
| 1 | Inventario, separación AP/entrada/motor y plan por perfil | Implementada; guarda intención sin aplicarla |
| 2 | Conectividad Ethernet/USB/otra radio y repetidor de una radio cuando sea compatible | Implementada en beta; validación física pendiente |
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

Guardar requiere un perfil AP ya creado con `thinkisp setup`. La intención se almacena por UUID en `network_profiles` dentro del estado privado existente, con versión de esquema. No almacena contraseñas. Guardar no modifica conexiones de NetworkManager. A partir de fase 2, el siguiente encendido aplica el plan guardado y mantiene el portal desactivado. Sin un plan guardado se conserva el arranque básico anterior.

El motor previsto puede ser `none`, `opennds` o `auto`. openNDS se detecta como candidato opcional; su adaptación operativa es fase 3. El motor no se descarga, habilita ni ejecuta en fase 1.

## Validación

Las pruebas usan instalaciones simuladas independientes para enlaces cableados/USB, distintas radios, una radio con/sin concurrencia, presupuestos incompatibles, varios canales, selección de rutas, errores de permisos y configuraciones dañadas. Se verifica aislamiento entre perfiles, rechazo de planes incompatibles y ausencia de comandos de mutación durante el inventario. También se comprueban CLI, editor, curses y empaquetado.

El diagnóstico del equipo de desarrollo sólo es una observación adicional; no define las capacidades generales del proyecto. La aprobación de fase 1 no equivale a tener un portal cautivo ni un repetidor operativos.

## Ejecución de fase 2

1. Configura el perfil AP con `thinkisp setup`.
2. Guarda la topología en F3 → Conectividad o con `thinkisp network plan --ap INTERFAZ_AP --upstream INTERFAZ_ENTRADA --mode auto --engine none --save`.
3. Enciende desde el panel o con `thinkisp on`. `thinkisp network inspect` muestra el estado de ejecución además del plan.
4. Apaga con `thinkisp off`. Si queda recuperación pendiente, usa `thinkisp recover` con el AP apagado.

En modo routed se utiliza el adaptador AP seleccionado. En repetidor, si entrada y AP son la misma interfaz se crea una interfaz AP virtual conservando la conexión cliente; si ya son dos interfaces del mismo PHY se utiliza el AP seleccionado. Se comprueba el presupuesto de interfaces existente, no sólo la lista de modos. Una radio que exige un canal único toma el del cliente, únicamente si está autorizado para AP. Una banda o canal fijados que contradigan el enlace se rechazan antes de modificar la red. No se fuerza ningún país ni se parchea el controlador.

La entrada debe tener conexión activa de NetworkManager, dirección IPv4 y ruta predeterminada IPv4 en la tabla principal. Se admiten enlaces Ethernet, USB, otra radio y otros enlaces que cumplan esos requisitos; no se garantiza compatibilidad de cualquier VPN o firewall. No se implementa NAT64. El AP usa IPv4 compartido de NetworkManager para DHCP, DNS y NAT; IPv6 se desactiva temporalmente. Una subred automática evita las direcciones y rutas existentes; una subred manual solapada se rechaza.

Las rutas por origen/interfaz limitan a los clientes a la entrada elegida, con prioridad anterior a otras reglas no locales. Una ruta inalcanzable impide usar otra WAN si desaparece la entrada. No cambia la ruta predeterminada del equipo. El vigilante comprueba conexión y canal y solicita el apagado ante cambios; si falta autorización para limpiar, conserva el registro y avisa.

Los cambios del perfil son temporales en NetworkManager. El registro `connectivity_runtime` guarda UUID propietario, propiedades originales, identidad de interfaz virtual y recursos de rutas. El apagado restaura el perfil y retira sólo los recursos identificados como propios. Si un recurso o perfil cambió externamente, la recuperación conserva el registro y explica el conflicto. Las operaciones se serializan con un bloqueo entre procesos. Los cambios del perfil, QoS y plan se realizan con la zona apagada para evitar persistir valores de sesión.

La prueba de integración crea exclusivamente interfaces ficticias dentro de un user/network namespace vacío. Verifica selección real de salida, cierre al eliminarla y conservación de otra WAN. Las pruebas de transacción simulan rechazo del controlador, fallos de rutas, cambios externos y recuperación. Esto no certifica asociación Wi-Fi, DHCP ni navegación con hardware real; esa validación sigue pendiente. El portal y sus plantillas pertenecen a las fases 3 y posteriores.
