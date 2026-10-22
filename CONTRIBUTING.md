# Contribuyendo a ThinkISP / Contributing to ThinkISP

¡Gracias por tu interés en contribuir a **ThinkISP**!  
ThinkISP es una suite de alto rendimiento para convertir laptops Linux en routers portátiles optimizados para ultra baja latencia, cero pérdida de paquetes y máxima velocidad.

---

## 📋 Tabla de Contenidos / Table of Contents
1. [Código de Conducta](#código-de-conducta)
2. [Cómo Empezar](#cómo-empezar)
3. [Estilo de Código y Estándares](#estilo-de-código-y-estándares)
4. [Ejecución de Pruebas](#ejecución-de-pruebas)
5. [Actualización de Autocompletado de Shell](#actualización-de-autocompletado-de-shell)
6. [Flujo de Pull Requests](#flujo-de-pull-requests)

---

## 🤝 Código de Conducta

Por favor mantén un trato respetuoso, constructivo y colaborativo con todos los miembros de la comunidad.

---

## 🛠️ Cómo Empezar

### Requisitos Previos
- **Python 3.9** o superior.
- **Herramientas de red Linux estándar**: `nmcli`, `iw`, `ip`, `tc`, `ethtool` (opcional: `qrencode`, `dig`, `notify-send`).
- **Git**.

ThinkISP está diseñado intencionalmente con **cero dependencias externas de `pip`**, utilizando únicamente la biblioteca estándar de Python y las herramientas nativas del kernel/sistema operativo.

### Clonar el Repositorio
```bash
git clone https://github.com/jssi/thinkisp.git
cd thinkisp
```

### Probar en Modo Desarrollo
Puedes probar los comandos directamente apuntando a `main.py`:
```bash
python3 main.py status
python3 main.py --help
```

---

## 🎨 Estilo de Código y Estándares

- **PEP 8**: Sigue las guías de estilo oficiales de Python.
- **Compatibilidad**: Todo el código debe ser compatible con Python 3.9 a 3.13+.
- **Sin Dependencias Pesadas**: Mantén la arquitectura ligera basada en la biblioteca estándar de Python (`argparse`, `curses`, `subprocess`, `socket`, `json`, `pathlib`).
- **Tratamiento de Errores**: Maneja de forma segura las excepciones cuando las herramientas del sistema (`nmcli`, `iw`) no estén presentes o fallen.
- **Mensajes de Commit**: Usa el formato [Conventional Commits](https://www.conventionalcommits.org/):
  - `feat: agrega soporte para modo canal DFS`
  - `fix: corrige lectura de clientes conectados en iw dev`
  - `docs: actualiza guía de instalación de zsh`
  - `test: agrega pruebas para subcomando band`

---

## 🧪 Ejecución de Pruebas

Antes de enviar cualquier cambio, asegúrate de que todos los archivos compilen y las pruebas unitarias pasen:

### 1. Comprobación de Sintaxis / Compilación
```bash
python3 -m py_compile $(git ls-files '*.py')
```

### 2. Ejecutar la Suite de Pruebas
```bash
python3 tests/run_tests.py
```
O usando el descubridor nativo de `unittest`:
```bash
python3 -m unittest discover tests
```

---

## 🐚 Actualización de Autocompletado de Shell

Si agregas nuevos subcomandos, banderas o argumentos al CLI en `main.py`, recuerda actualizar los scripts de autocompletado correspondientes en `extras/completions/`:
- **Zsh**: `extras/completions/_thinkisp`
- **Bash**: `extras/completions/thinkisp.bash`
- **Fish**: `extras/completions/thinkisp.fish`

---

## 🚀 Flujo de Pull Requests

1. Haz un fork del repositorio y crea una rama para tu característica o corrección:
   ```bash
   git checkout -b feat/mi-nueva-caracteristica
   ```
2. Realiza tus cambios asegurando que las pruebas pasen.
3. Haz commit de tus cambios con mensajes descriptivos.
4. Haz push a tu fork:
   ```bash
   git push origin feat/mi-nueva-caracteristica
   ```
5. Abre un **Pull Request** hacia la rama `main` del repositorio oficial.
6. La integración continua de GitHub Actions validará automáticamente tu código en múltiples versiones de Python (3.9 - 3.13).

¡Agradecemos mucho tus contribuciones para hacer de ThinkISP la mejor suite de Wi-Fi hotspot en Linux!
