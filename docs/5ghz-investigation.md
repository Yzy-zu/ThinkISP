# Investigación del AP de 5 GHz

Investigación del 22 de octubre de 2026. La primera consulta fue de sólo lectura. Después se compiló y cargó temporalmente un módulo experimental con autorización del usuario; el resultado se documenta más abajo. Después se intentó activar un AP de 5 GHz; el firmware falló y la conexión cliente fue restaurada automáticamente.

## Qué se confirmó en el equipo

| Dato | Observación |
| --- | --- |
| Adaptador | Intel Dual Band Wireless-AC 8265, PCI 8086:24fd, subsistema 8086:0010. |
| Controlador | iwlwifi con el módulo de operación iwlmvm. |
| Kernel | 7.2.11-arch1-1. |
| Firmware | 36.c8e8e144.0, 8265-36.ucode. |
| Enlace cliente durante la consulta | Canal 44, 5220 MHz, ancho de 80 MHz. El dispositivo sí estaba transmitiendo y recibiendo en 5 GHz. |
| Canales de 5 GHz | Todos los no deshabilitados anunciaban `no IR`; algunos también requerían detección de radar. |
| Regulación | Radio self-managed, country 00. |
| Concurrencia anunciada | La combinación que incluye AP admite un solo canal simultáneo. |

Estos datos son observaciones, no constantes del programa ni reglas por marca.

## Explicación

La [ficha oficial de Intel](https://www.intel.com/content/www/us/en/products/sku/94150/intel-dual-band-wirelessac-8265/specifications.html) confirma que la 8265 admite 2.4 y 5 GHz. La [documentación oficial de iwlwifi](https://wireless.docs.kernel.org/en/latest/en/users/drivers/iwlwifi.html) distingue el modo cliente del modo AP y documenta la limitación de AP de 5 GHz por LAR en iwlmvm.

LAR significa Location Aware Regulatory: interviene en las restricciones regulatorias de la radio y su firmware. `no IR` restringe el inicio de transmisiones autónomas, incluidos los beacons de un AP. No impide que una estación ya conectada responda y transmita datos en 5 GHz. La [documentación del subsistema regulatorio](https://wireless.docs.kernel.org/en/latest/en/developers/regulatory/processing_rules.html) explica esa distinción y las condiciones limitadas de relajación para P2P.

Por tanto, el diagnóstico no demuestra incapacidad física de la radio para transmitir en 5 GHz. Demuestra que el stack actualmente instalado no anuncia canales utilizables para iniciar un AP normal en esa banda. Que un teléfono admita hotspot de 5 GHz depende de su propia combinación de hardware, firmware y controladores.

## Vía experimental encontrada

El proyecto [linux-wifi-hotspot documenta un parche para iwlmvm](https://github.com/lakinduakash/linux-wifi-hotspot/blob/master/docs/howto/intel-5ghz-lar.md). Revisé el [diff](https://github.com/lakinduakash/linux-wifi-hotspot/blob/master/util/iwlwifi-lar-disable/dkms/patches/lar_disable.patch): añade una opción `lar_disable` a iwlmvm y modifica la comprobación de soporte LAR. El objetivo es que se apliquen las reglas regulatorias del kernel en lugar del dominio self-managed.

Es una alternativa para investigar en esta familia de controlador, no una solución certificada para esta tarjeta, firmware y kernel. Requiere construir un módulo modificado y configurar el país real y sus canales permitidos. El parámetro no existe en los módulos actuales: escribirlo en una configuración no aplica el parche. Cambiar sólo el país con iw tampoco garantiza retirar las restricciones de un dispositivo self-managed.

Los scripts de construcción e instalación del proyecto requieren código fuente del kernel, permisos administrativos y, si corresponde, firma del módulo. Se utilizó una preparación local separada, sin ejecutar el instalador del proyecto externo. La compilación y prueba realizadas se describen a continuación.

Además, para compartir una conexión Wi-Fi de entrada mediante una segunda interfaz en esta misma radio, el AP tiene que seguir el canal de la conexión cliente según la combinación anunciada. La implementación actual de thinkISP reutiliza la interfaz seleccionada para AP; no crea un repetidor con interfaces cliente y AP simultáneas. Solucionar LAR no implementa por sí solo esa topología.

## Conclusión verificable

La radio opera como cliente en 5 GHz. El parche compila, carga y permite anunciar canales AP de esa banda, pero **la prueba real de AP falló en el firmware** de esta Intel 8265 con kernel 7.2.11 y firmware 36.c8e8e144.0. No se considera una solución funcional para esta combinación. No demuestra que todos los equipos con iwlmvm fallen ni que toda alternativa futura sea imposible.

## Resultado de la prueba temporal

- Fuentes oficiales de Arch: etiqueta `v7.2.11-arch1`, commit `64a90897d3c539033f24105968e3513957e70819`.
- Parche de linux-wifi-hotspot fijado al commit `ef0cbae29ad35def5949c979b18a3957499ccc2f`; SHA256 `710635eda0c8afcfe264e9a6a122e3716220c0ea1f5e426a33d8b5ceb417fda3`.
- Aplicación sin fuzz, compilación contra los headers exactos del kernel instalado y `vermagic` idéntico al módulo original. Secure Boot y la exigencia de firma estaban desactivados.
- Carga mediante `insmod` con `lar_disable=1`; se verificó `lar_disable=Y` en sysfs. El módulo original en disco no se modificó.
- País físico confirmado por el usuario: México. Se solicitó `MX` después de restaurar la conexión cliente. El router de entrada anunciaba `US`; el dominio resultante apareció como `98`, la intersección regulatoria identificada así por [cfg80211 en Linux](https://github.com/torvalds/linux/blob/master/net/wireless/reg.c). No se configuró un país alternativo para ampliar permisos.
- Canales anunciados sin `no IR` ni DFS: **36, 40, 44, 48, 149, 153, 157, 161 y 165**. Los canales inferiores anunciaban 17 dBm y los superiores 22 dBm; son límites observados, no ajustes de potencia aplicados.
- El diagnóstico real de thinkISP finalizó con `core_checks_passed=true`, bandas `a` y `bg`, y anchos anunciados de 20/40/80 MHz para 5 GHz. La disponibilidad de un ancho depende también del canal y no confirma su activación.
- La conexión cliente se restauró por UUID sin registrar credenciales. En esta primera etapa no se crearon perfiles AP. Posteriormente se hizo el intento de activación descrito abajo.

Los ensayos usan servicios transitorios de systemd con recuperación independiente de la terminal. Los primeros se revirtieron automáticamente: uno no conservó el paso exacto del fallo; los siguientes fueron rechazados por comprobaciones demasiado estrictas del verificador local (frecuencias decimales y dominio `98`). El ensayo final terminó en `success`.

### Estado y reversión en este equipo

El parche permanece **sólo en memoria para esta sesión**. Reiniciar o recargar el controlador normalmente vuelve al módulo original. No se escribieron opciones en `/etc/modprobe.d`, no se instalaron overrides en `/usr/lib/modules` y no se reconstruyó el initramfs.

Estado del ensayo final: `/var/lib/thinkisp-iwlmvm-trial-4/status.json`. Capturas de radio y regulación en ese mismo directorio. Fuentes, logs, procedencia y diagnóstico en `.local/iwlwifi-lar/`, excluido del repositorio. Son artefactos de este equipo, no requisitos ni constantes del programa.

Para volver al controlador original sin reiniciar (interrumpe brevemente Wi-Fi y restaura la conexión registrada):

```sh
sudo /usr/bin/python3 /var/lib/thinkisp-iwlmvm-trial-4/trial.py rollback
```

Tras reiniciar, esta prueba necesita una nueva carga explícita; el script local de ensayo protege contra ejecuciones repetidas y no es un instalador persistente ni una solución para otros kernels.


## Prueba real de activación posterior

Se creó un perfil temporal con WPA2-PSK, banda `a`, canal 44 (5220 MHz), IPv4 compartida y autoconexión desactivada. El canal se eligió entre los anunciados sin `no IR` ni DFS. La clave aleatoria no se registró en comandos ni documentación. No se tocaron perfiles habituales ni se hizo persistente el módulo modificado.

El primer ensayo se detuvo porque el perfil se cargó fuera de los directorios de conexiones reconocidos por NetworkManager. Se corrigió usando `/run/NetworkManager/system-connections` y verificando su UUID antes de desconectar la conexión cliente.

En el segundo ensayo NetworkManager sí inició la activación, pero a las 08:52:06 del 22 de octubre de 2026 el kernel registró:

```text
Microcode SW error detected. Restarting 0x82000000.
0x000014FC | ADVANCED_SYSASSERT
FW error in SYNC CMD BINDING_CONTEXT_CMD
Failed to send binding (action:1): -5
```

wpa_supplicant registró `Failed to set beacon parameters` y `Failed to initialize AP interface`. NetworkManager terminó con `supplicant-timeout`. No se confirmó un AP activo ni se alcanzaron las pruebas de asociación, DHCP o navegación. No se atribuye el fallo a potencia, país o contraseña: la evidencia directa es un fallo del firmware durante la activación.

El servicio independiente eliminó el perfil de prueba y restauró la conexión cliente. Resultado: `/var/lib/thinkisp-5ghz-ap-trial-2/status.json`, `status=failed`, `connection_restored=true`.

Se intentó además recargar el controlador original. La sesión ya no disponía de agente gráfico de PolicyKit ni de autorización sudo; el fallback pidió una contraseña en una terminal del agente y se canceló sin solicitar credenciales al usuario. Por ello **el módulo experimental sigue cargado en memoria**, aunque el AP de prueba fue eliminado y la conexión cliente está restaurada. Para retirarlo se necesita ejecutar el comando de reversión anterior en una terminal del usuario o reiniciar. No se instaló ningún cambio persistente del controlador.
