"""thinkISP Captive Portal HTTP Server and Page Renderer.

Zero external dependencies (Python stdlib only).
Handles captive detection redirects, form presentation, token validation,
and authorization state reporting in cooperation with the local supervisor/broker.
"""

import html
import http.server
import ipaddress
import queue
import re
import socket
import threading
import urllib.parse
from typing import Any, Callable, Dict, Optional, Tuple

from thinkisp.errors import ThinkISPError

# URIs that land directly on the captive portal page
PORTAL_PATHS = frozenset({
    "/",
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/connecttest.txt",
    "/ncsi.txt",
    "/canonical.html",
    "/success.txt",
})

# Form constraints
MAX_FORM_SIZE = 2048
MAX_TOKEN_LENGTH = 128
SOCKET_TIMEOUT = 5.0

# Simple URLsafe charset: letters, numbers, _, -, ., ~
TOKEN_REGEX = re.compile(r"^[A-Za-z0-9_.~-]+$")

# Host header validation regex: host/IPv4, optional port
HOST_REGEX = re.compile(r"^[a-zA-Z0-9._-]+(:[0-9]{1,5})?$")


def render_page(
    label: str,
    token: Optional[str] = None,
    action_url: str = "/accept",
    authorized: bool = False,
    error: Optional[str] = None,
) -> bytes:
    """Render a responsive, self-contained captive portal page in Spanish.

    Args:
        label: Dynamic portal display label (escaped in heading).
        token: Unguessable challenge token string or None.
        action_url: Form submission action URL.
        authorized: If True, renders authorized status page without form.
        error: Optional Spanish error message to display.

    Returns:
        UTF-8 encoded HTML bytes.
    """
    label_text = str(label) if label is not None else "thinkISP"
    escaped_label = html.escape(label_text, quote=True)
    escaped_action = html.escape(action_url or "/accept", quote=True)
    escaped_token = html.escape(token or "", quote=True)
    escaped_error = html.escape(error, quote=True) if error else None

    if authorized:
        main_content = """    <div class="status-card status-success" role="status" aria-live="polite">
      <div class="status-icon" aria-hidden="true">&#x2714;</div>
      <h2>Conexión habilitada</h2>
      <p>Su dispositivo ya se encuentra autorizado para navegar en la red.</p>
    </div>
"""
    elif error and not token:
        main_content = f"""    <div class="alert-box alert-error" role="alert">
      <div class="alert-title">Aviso del portal</div>
      <p>{escaped_error}</p>
    </div>
    <p class="info-text">Si el problema persiste, intente reconectarse a la red.</p>
"""
    else:
        error_snippet = ""
        if escaped_error:
            error_snippet = f"""    <div class="alert-box alert-error" role="alert">
      <p>{escaped_error}</p>
    </div>
"""
        main_content = f"""{error_snippet}    <p class="info-text">Para acceder a la red y a Internet, pulse el botón a continuación.</p>
    <form action="{escaped_action}" method="POST" enctype="application/x-www-form-urlencoded" class="portal-form">
      <input type="hidden" name="token" value="{escaped_token}">
      <button type="submit" class="btn-primary" aria-label="Continuar navegación">Continuar</button>
    </form>
"""

    html_text = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <title>{escaped_label} - thinkISP</title>
  <style>
    :root {{
      --bg-color: #f1f5f9;
      --card-bg: #ffffff;
      --border-color: #e2e8f0;
      --text-main: #0f172a;
      --text-muted: #64748b;
      --btn-bg: #2563eb;
      --btn-hover: #1d4ed8;
      --btn-text: #ffffff;
      --err-bg: #fef2f2;
      --err-border: #fecaca;
      --err-text: #991b1b;
      --suc-bg: #f0fdf4;
      --suc-border: #bbf7d0;
      --suc-text: #166534;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg-color: #0b0f19;
        --card-bg: #151e2e;
        --border-color: #1e293b;
        --text-main: #f8fafc;
        --text-muted: #94a3b8;
        --btn-bg: #3b82f6;
        --btn-hover: #2563eb;
        --btn-text: #ffffff;
        --err-bg: #450a0a;
        --err-border: #7f1d1d;
        --err-text: #fca5a5;
        --suc-bg: #052e16;
        --suc-border: #14532d;
        --suc-text: #86efac;
      }}
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg-color);
      color: var(--text-main);
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      padding: 16px;
      line-height: 1.5;
    }}
    .card {{
      background-color: var(--card-bg);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      max-width: 440px;
      width: 100%;
      padding: 32px 24px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1);
      text-align: center;
    }}
    .header {{ margin-bottom: 24px; }}
    .portal-title {{
      font-size: 1.35rem;
      font-weight: 700;
      color: var(--text-main);
      word-break: break-word;
      margin-bottom: 6px;
    }}
    .portal-subtitle {{
      font-size: 0.875rem;
      color: var(--text-muted);
    }}
    .info-text {{
      font-size: 0.95rem;
      color: var(--text-main);
      margin-bottom: 24px;
    }}
    .portal-form {{ margin-top: 12px; }}
    .btn-primary {{
      display: block;
      width: 100%;
      background-color: var(--btn-bg);
      color: var(--btn-text);
      font-size: 1.05rem;
      font-weight: 600;
      padding: 14px 20px;
      border: none;
      border-radius: 8px;
      cursor: pointer;
      transition: background-color 0.15s ease-in-out;
    }}
    .btn-primary:hover, .btn-primary:focus {{
      background-color: var(--btn-hover);
      outline: none;
    }}
    .alert-box {{
      background-color: var(--err-bg);
      border: 1px solid var(--err-border);
      color: var(--err-text);
      border-radius: 8px;
      padding: 14px 16px;
      margin-bottom: 20px;
      font-size: 0.9rem;
      text-align: left;
    }}
    .alert-title {{
      font-weight: 700;
      margin-bottom: 4px;
    }}
    .status-card {{
      background-color: var(--suc-bg);
      border: 1px solid var(--suc-border);
      color: var(--suc-text);
      border-radius: 8px;
      padding: 24px 16px;
      margin-bottom: 16px;
    }}
    .status-icon {{
      font-size: 2rem;
      line-height: 1;
      margin-bottom: 12px;
    }}
    .status-card h2 {{
      font-size: 1.15rem;
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .status-card p {{
      font-size: 0.9rem;
    }}
    .footer {{
      margin-top: 28px;
      padding-top: 16px;
      border-top: 1px solid var(--border-color);
      font-size: 0.75rem;
      color: var(--text-muted);
    }}
  </style>
</head>
<body>
  <main class="card">
    <header class="header">
      <h1 class="portal-title">{escaped_label} &bull; thinkISP</h1>
      <p class="portal-subtitle">Portal de acceso a la red</p>
    </header>
{main_content}    <footer class="footer">
      <p>thinkISP &bull; Acceso a la red local e Internet</p>
    </footer>
  </main>
</body>
</html>"""
    return html_text.encode("utf-8")


class CaptivePortalHandler(http.server.BaseHTTPRequestHandler):
    """HTTP Request Handler for thinkISP Captive Portal."""

    server_version = "thinkISP-Portal/1.0"
    sys_version = ""
    timeout = SOCKET_TIMEOUT
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        # Suppress logging to avoid leaking request URIs, nonces, or payloads
        pass

    def log_request(self, code='-', size='-'):
        pass

    def log_error(self, format, *args):
        pass

    def _get_peer_ipv4(self) -> Optional[str]:
        """Extract peer IPv4 address solely from the underlying socket client_address."""
        if not isinstance(self.client_address, (tuple, list)) or not self.client_address:
            return None
        peer = self.client_address[0]
        try:
            ip = ipaddress.ip_address(peer)
            if ip.version != 4:
                return None
            return str(ip)
        except ValueError:
            return None

    def _send_security_headers(self):
        """Send required security and anti-caching headers on all responses."""
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none';",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        self.close_connection = True

    def _send_error_response(self, code: int, message: str, is_head: bool = False):
        """Send an error page with appropriate status and security headers."""
        body = render_page(
            label=self.server.label,
            token=None,
            action_url=self.server.canonical_action_url,
            authorized=False,
            error=message,
        )
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._send_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not is_head:
            try:
                self.wfile.write(body)
            except Exception:
                pass

    def send_error(self, code: int, message: Optional[str] = None, explain: Optional[str] = None):
        msg = message if message else f"Error HTTP {code}"
        self._send_error_response(code, msg)

    def _safe_parse_path(self) -> Optional[str]:
        """Safely parse request target path, protecting against malformed URIs.

        Returns:
            Decoded path string, or None if malformed or containing null/control characters.
        """
        if not hasattr(self, "path") or not isinstance(self.path, str):
            return None
        if "\x00" in self.path or any(ord(c) < 32 for c in self.path):
            return None
        try:
            parsed = urllib.parse.urlsplit(self.path)
            return parsed.path
        except (ValueError, UnicodeError):
            return None

    def _validate_host(self) -> str:
        """Validate Host header.

        Returns:
            'canonical': Host matches canonical portal gateway:port (or gateway alone if port 80).
            'external': Host is valid syntax but non-canonical (intercepted traffic).
            'invalid': Missing, duplicate, conflicting, or malformed Host.
        """
        host_headers = self.headers.get_all("Host")
        if not host_headers or len(host_headers) != 1:
            return "invalid"

        host_val = host_headers[0].strip()
        if not host_val or not HOST_REGEX.match(host_val) or host_val.count(":") > 1:
            return "invalid"

        if host_val.lower() == self.server.canonical_host.lower():
            return "canonical"

        if self.server.allow_gateway_host and host_val.lower() == self.server.gateway.lower():
            return "canonical"

        return "external"

    def _validate_origin(self) -> Optional[Tuple[int, str]]:
        """Validate Origin header.

        Returns:
            None if valid (or omitted).
            (status_code, error_message) tuple if invalid.
        """
        origin_headers = self.headers.get_all("Origin")
        if not origin_headers:
            return None  # Origin omission supported for captive mini-browsers

        if len(origin_headers) > 1:
            return (400, "Múltiples encabezados Origin no permitidos.")

        origin_val = origin_headers[0].strip()
        if not origin_val:
            return (400, "Encabezado Origin vacío no permitido.")

        origin_clean = origin_val.lower()
        allowed = {self.server.canonical_origin.lower()}
        if self.server.allow_gateway_host:
            allowed.add(f"http://{self.server.gateway}".lower())

        if origin_clean not in allowed:
            return (403, "Origen de solicitud no autorizado.")

        return None

    def _validate_content_type(self) -> Optional[Tuple[int, str]]:
        """Validate Content-Type header.

        Returns:
            None if valid.
            (status_code, error_message) tuple if invalid.
        """
        ct_headers = self.headers.get_all("Content-Type")
        if not ct_headers:
            return (415, "Tipo de contenido ausente; se requiere application/x-www-form-urlencoded.")

        if len(ct_headers) > 1:
            return (400, "Múltiples encabezados Content-Type no permitidos.")

        ct_header = ct_headers[0].strip()
        ct_parts = [p.strip() for p in ct_header.split(";")]
        if ct_parts[0].lower() != "application/x-www-form-urlencoded":
            return (415, "Tipo de contenido no admitido. Se requiere application/x-www-form-urlencoded.")

        for param in ct_parts[1:]:
            if not param:
                continue
            param_l = param.lower()
            if param_l.startswith("charset="):
                charset = param_l.split("=", 1)[1].strip().strip('"').strip("'")
                if charset not in ("utf-8", "ascii", "iso-8859-1", "us-ascii"):
                    return (400, "Juego de caracteres no admitido.")
            else:
                return (400, "Parámetros de Content-Type no válidos.")

        return None

    def _redirect_to_canonical(self, is_head: bool = False):
        """Safely send HTTP 303 redirect to canonical portal URL."""
        self.send_response(303)
        self.send_header("Location", self.server.canonical_origin + "/")
        self._send_security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        try:
            self._handle_get()
        except Exception:
            self._send_error_response(400, "Error al procesar la solicitud.")

    def _handle_get(self):
        peer_ip = self._get_peer_ipv4()
        if not peer_ip:
            self._send_error_response(403, "Acceso denegado: solo se admite tráfico IPv4.")
            return

        host_status = self._validate_host()
        if host_status == "invalid":
            self._send_error_response(400, "Encabezado Host inválido o ausente.")
            return

        if host_status == "external":
            self._redirect_to_canonical()
            return

        path = self._safe_parse_path()
        if path is None:
            self._send_error_response(400, "Destino de solicitud malformado.")
            return

        if path not in PORTAL_PATHS:
            self._redirect_to_canonical()
            return

        # Query backend for client status
        try:
            status_res = self.server.backend("client_status", {"ip": peer_ip})
        except ThinkISPError:
            self._send_error_response(503, "Servicio de autenticación no disponible temporalmente.")
            return
        except Exception:
            self._send_error_response(503, "Error de comunicación con el servicio del portal.")
            return

        if not isinstance(status_res, dict):
            self._send_error_response(503, "Respuesta inválida del servicio de autenticación.")
            return

        if status_res.get("authorized") is True:
            # Client already authorized
            body = render_page(
                label=self.server.label,
                token=None,
                action_url=self.server.canonical_action_url,
                authorized=True,
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._send_security_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass
            return

        # Client not authorized: obtain challenge
        try:
            challenge_res = self.server.backend("challenge", {"ip": peer_ip})
        except ThinkISPError:
            self._send_error_response(503, "No se pudo obtener el token de acceso.")
            return
        except Exception:
            self._send_error_response(503, "Error interno al solicitar el desafío.")
            return

        if not isinstance(challenge_res, dict) or "token" not in challenge_res:
            self._send_error_response(503, "Respuesta de desafío inválida.")
            return

        token = challenge_res["token"]
        if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH or not TOKEN_REGEX.match(token):
            self._send_error_response(503, "Token de desafío inválido.")
            return

        body = render_page(
            label=self.server.label,
            token=token,
            action_url=self.server.canonical_action_url,
            authorized=False,
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._send_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_HEAD(self):
        try:
            self._handle_head()
        except Exception:
            self._send_error_response(400, "Error al procesar la solicitud HEAD.", is_head=True)

    def _handle_head(self):
        peer_ip = self._get_peer_ipv4()
        if not peer_ip:
            self._send_error_response(403, "Acceso denegado: solo se admite tráfico IPv4.", is_head=True)
            return

        host_status = self._validate_host()
        if host_status == "invalid":
            self._send_error_response(400, "Encabezado Host inválido o ausente.", is_head=True)
            return

        if host_status == "external":
            self._redirect_to_canonical(is_head=True)
            return

        path = self._safe_parse_path()
        if path is None:
            self._send_error_response(400, "Destino de solicitud malformado.", is_head=True)
            return

        if path not in PORTAL_PATHS:
            self._redirect_to_canonical(is_head=True)
            return

        # Safe HEAD response without state mutation or challenge creation
        try:
            status_res = self.server.backend("client_status", {"ip": peer_ip})
            authorized = bool(isinstance(status_res, dict) and status_res.get("authorized"))
        except Exception:
            self._send_error_response(503, "Servicio de autenticación no disponible.", is_head=True)
            return

        body = render_page(
            label=self.server.label,
            token="" if not authorized else None,
            action_url=self.server.canonical_action_url,
            authorized=authorized,
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._send_security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

    def do_POST(self):
        try:
            self._handle_post()
        except Exception:
            self._send_error_response(400, "Error al procesar la solicitud POST.")

    def _handle_post(self):
        peer_ip = self._get_peer_ipv4()
        if not peer_ip:
            self._send_error_response(403, "Acceso denegado: solo se admite tráfico IPv4.")
            return

        host_status = self._validate_host()
        if host_status == "invalid":
            self._send_error_response(400, "Encabezado Host inválido o ausente.")
            return

        if host_status == "external":
            self._send_error_response(400, "Host no coincide con la dirección canónica del portal.")
            return

        path = self._safe_parse_path()
        if path is None:
            self._send_error_response(400, "Destino de solicitud malformado.")
            return

        if path != "/accept":
            self._send_error_response(404, "Ruta no encontrada para POST.")
            return

        # Validate Origin header
        origin_err = self._validate_origin()
        if origin_err:
            code, msg = origin_err
            self._send_error_response(code, msg)
            return

        # Validate Content-Type
        ct_err = self._validate_content_type()
        if ct_err:
            code, msg = ct_err
            self._send_error_response(code, msg)
            return

        # Framing validation BEFORE reading
        if self.headers.get("Transfer-Encoding") is not None:
            self._send_error_response(400, "Transfer-Encoding por fragmentos (chunked) no admitido.")
            return

        cl_headers = self.headers.get_all("Content-Length")
        if not cl_headers or len(cl_headers) != 1:
            self._send_error_response(400, "Encabezado Content-Length ausente o ambiguo.")
            return

        cl_str = cl_headers[0].strip()
        if not cl_str.isdigit():
            self._send_error_response(400, "Content-Length no numérico.")
            return

        try:
            content_length = int(cl_str)
        except ValueError:
            self._send_error_response(400, "Content-Length no válido.")
            return

        if content_length <= 0:
            self._send_error_response(400, "Cuerpo del formulario vacío.")
            return

        if content_length > MAX_FORM_SIZE:
            self._send_error_response(413, f"Cuerpo del formulario excede el límite de {MAX_FORM_SIZE} bytes.")
            return

        # Read socket with bounded timeout
        self.connection.settimeout(SOCKET_TIMEOUT)

        try:
            raw_body = self.rfile.read(content_length)
        except socket.timeout:
            self._send_error_response(408, "Tiempo de espera de lectura agotado.")
            return
        except Exception:
            self._send_error_response(400, "Error al leer el cuerpo de la solicitud.")
            return

        if len(raw_body) != content_length:
            self._send_error_response(400, "Cuerpo de solicitud incompleto.")
            return

        try:
            body_text = raw_body.decode("utf-8")
        except UnicodeDecodeError:
            try:
                body_text = raw_body.decode("iso-8859-1")
            except UnicodeDecodeError:
                self._send_error_response(400, "Cuerpo de solicitud no codificable como texto.")
                return

        try:
            fields = urllib.parse.parse_qsl(body_text, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            self._send_error_response(400, "Formato urlencoded inválido.")
            return

        if len(fields) != 1:
            self._send_error_response(400, "El formulario debe contener exactamente un campo.")
            return

        field_name, token_val = fields[0]
        if field_name != "token":
            self._send_error_response(400, "Campo de formulario inválido; se requiere 'token'.")
            return

        if not token_val:
            self._send_error_response(400, "El token no puede estar vacío.")
            return

        if len(token_val) > MAX_TOKEN_LENGTH:
            self._send_error_response(400, f"El token excede el tamaño máximo permitido ({MAX_TOKEN_LENGTH} caracteres).")
            return

        if not TOKEN_REGEX.match(token_val):
            self._send_error_response(400, "Caracteres no válidos en el token.")
            return

        # Submit authorization request to broker
        try:
            auth_res = self.server.backend("accept", {"ip": peer_ip, "token": token_val})
        except ThinkISPError as exc:
            msg = str(exc).lower()
            if any(k in msg for k in ("no disponible", "downtime", "down", "desconectado", "unavailable", "timeout")):
                self._send_error_response(503, "Servicio de autenticación no disponible temporalmente.")
            else:
                self._send_error_response(403, "Autorización denegada: token no válido o caducado.")
            return
        except Exception:
            self._send_error_response(503, "Servicio de autenticación no disponible temporalmente.")
            return

        if not isinstance(auth_res, dict) or auth_res.get("authorized") is not True:
            self._send_error_response(403, "Autorización rechazada.")
            return

        # Broker accepted: HTTP 303 redirect to canonical /
        self.send_response(303)
        self.send_header("Location", self.server.canonical_origin + "/")
        self._send_security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD, POST")
        self._send_security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PUT(self):
        self._send_method_not_allowed()

    def do_DELETE(self):
        self._send_method_not_allowed()

    def do_PATCH(self):
        self._send_method_not_allowed()

    def _send_method_not_allowed(self):
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD, POST")
        self._send_security_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()


class CaptivePortalServer(http.server.HTTPServer):
    """HTTPServer implementation for the captive portal with bounded workers and resource tracking."""

    def __init__(
        self,
        listener: socket.socket,
        gateway: str,
        port: int,
        label: str,
        backend: Callable[[str, Dict[str, Any]], Dict[str, Any]],
        max_workers: int = 16,
    ):
        # 1. Initialize tracking structures before any potential failure
        self._close_lock = threading.RLock()
        self._closed = False
        self._shutdown_event = threading.Event()
        self._sockets_lock = threading.Lock()
        self._active_sockets = set()
        self._workers = []
        self.work_queue = queue.Queue(maxsize=16)

        # 2. Strict validation of arguments before starting threads or binding
        # Gateway
        if isinstance(gateway, bool) or not isinstance(gateway, str):
            raise ValueError(f"Gateway debe ser una cadena IPv4: {gateway!r}")
        try:
            ip_obj = ipaddress.IPv4Address(gateway)
            self.gateway = str(ip_obj)
        except Exception as exc:
            raise ValueError(f"Gateway debe ser una dirección IPv4 numérica estricta: {gateway}") from exc

        # Port
        if isinstance(port, bool) or not isinstance(port, int):
            raise ValueError(f"Puerto debe ser entero, no booleano ni otro tipo: {port!r}")
        if not (1 <= port <= 65535):
            raise ValueError(f"Puerto fuera de rango (1-65535): {port}")
        self.port = port

        # max_workers (integer 1..64, not bool)
        if isinstance(max_workers, bool) or not isinstance(max_workers, int):
            raise ValueError(f"max_workers debe ser entero, no booleano: {max_workers!r}")
        if not (1 <= max_workers <= 64):
            raise ValueError(f"max_workers fuera de rango (1-64): {max_workers}")
        self.max_workers = max_workers

        # backend
        if not callable(backend):
            raise ValueError("backend debe ser un invocable")
        self.backend = backend

        # Listener socket validation
        if not isinstance(listener, socket.socket):
            raise ValueError(f"Listener debe ser una instancia de socket.socket: {listener!r}")
        if listener.family != socket.AF_INET:
            raise ValueError(f"Listener debe tener familia AF_INET: {listener.family}")
        if listener.type != socket.SOCK_STREAM:
            raise ValueError(f"Listener debe ser de tipo SOCK_STREAM: {listener.type}")

        try:
            is_listening = listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
            if not is_listening:
                raise ValueError("Listener debe estar en estado de escucha (listen)")
        except OSError as exc:
            raise ValueError(f"No se pudo consultar estado de escucha del listener: {exc}") from exc

        try:
            bound_addr = listener.getsockname()
        except OSError as exc:
            raise ValueError(f"No se pudo obtener getsockname() del listener: {exc}") from exc

        if not isinstance(bound_addr, tuple) or len(bound_addr) < 2:
            raise ValueError(f"Dirección de listener inválida: {bound_addr!r}")

        bound_host, bound_port = bound_addr[0], bound_addr[1]
        if bound_port != self.port:
            raise ValueError(
                f"El puerto del listener ({bound_port}) no coincide con el puerto suministrado ({self.port})"
            )

        # Retain 0.0.0.0 prebound listener support for SO_BINDTODEVICE; never rebind.
        # Do not require arbitrary binding to gateway before AP assigned.
        self.label = str(label) if label is not None else "thinkISP"
        self.canonical_origin = f"http://{self.gateway}:{self.port}"
        self.canonical_action_url = f"{self.canonical_origin}/accept"
        self.canonical_host = f"{self.gateway}:{self.port}"
        self.allow_gateway_host = (self.port == 80)

        # 3. Initialize TCPServer without bind and activate
        super().__init__((self.gateway, self.port), CaptivePortalHandler, bind_and_activate=False)

        # Close dummy socket created by TCPServer.__init__
        try:
            self.socket.close()
        except Exception:
            pass

        self.socket = listener
        self.server_port = self.port
        self.server_name = self.gateway
        self.server_address = bound_addr

        # 4. Allocate queue and start worker threads
        self.work_queue = queue.Queue(maxsize=self.max_workers)
        for i in range(self.max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"PortalWorker-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)

    def verify_request(self, request, client_address) -> bool:
        if not isinstance(client_address, (tuple, list)) or not client_address:
            return False
        peer = client_address[0]
        try:
            ip = ipaddress.ip_address(peer)
            if ip.version != 4:
                return False
        except ValueError:
            return False
        return True

    def process_request(self, request, client_address):
        with self._sockets_lock:
            if self._closed or self._shutdown_event.is_set():
                try:
                    request.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    request.close()
                except Exception:
                    pass
                return
            self._active_sockets.add(request)

        try:
            self.work_queue.put_nowait((request, client_address))
        except queue.Full:
            self._reject_overload(request)

    def _worker_loop(self):
        while not self._shutdown_event.is_set():
            try:
                item = self.work_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                break
            request, client_address = item
            try:
                self.finish_request(request, client_address)
            except Exception:
                self.handle_error(request, client_address)
            finally:
                with self._sockets_lock:
                    self._active_sockets.discard(request)
                try:
                    self.shutdown_request(request)
                except Exception:
                    pass
                self.work_queue.task_done()

    def _reject_overload(self, request):
        try:
            request.settimeout(2.0)
            body = (
                "<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"utf-8\">"
                "<title>Servidor ocupado</title></head><body><h1>503 Servicio no disponible</h1>"
                "<p>Demasiadas solicitudes simultáneas. Inténtelo de nuevo en unos momentos.</p></body></html>"
            ).encode("utf-8")
            resp = (
                f"HTTP/1.1 503 Service Unavailable\r\n"
                f"Content-Type: text/html; charset=utf-8\r\n"
                f"Connection: close\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode("ascii") + body
            request.sendall(resp)
        except Exception:
            pass
        finally:
            with self._sockets_lock:
                self._active_sockets.discard(request)
            self.shutdown_request(request)

    def handle_error(self, request, client_address):
        # Suppress stack traces to avoid leaking internal state or credentials
        pass

    def server_close(self):
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

            self._shutdown_event.set()

            # 1. Close listening socket so no new connections are accepted
            try:
                super().server_close()
            except Exception:
                pass

            # 2. Drain queued connections and shutdown/close them
            while True:
                try:
                    item = self.work_queue.get_nowait()
                    if item is not None:
                        req_sock, _ = item
                        with self._sockets_lock:
                            self._active_sockets.discard(req_sock)
                        try:
                            req_sock.shutdown(socket.SHUT_RDWR)
                        except Exception:
                            pass
                        try:
                            req_sock.close()
                        except Exception:
                            pass
                except queue.Empty:
                    break

            # 3. Enqueue sentinels to wake all worker threads
            for _ in self._workers:
                try:
                    self.work_queue.put_nowait(None)
                except Exception:
                    pass

            # 4. Shutdown and close any active sockets currently in-flight
            with self._sockets_lock:
                active_list = list(self._active_sockets)
                self._active_sockets.clear()

            for s in active_list:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    s.close()
                except Exception:
                    pass

            # 5. Bounded join of worker threads (skip self to prevent deadlock)
            current = threading.current_thread()
            for t in self._workers:
                if t != current and t.is_alive():
                    t.join(timeout=1.0)


def make_server(
    listener: socket.socket,
    gateway: str,
    port: int,
    label: str,
    backend: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    max_workers: int = 16,
) -> CaptivePortalServer:
    """Create an HTTPServer-compatible captive portal server wrapping the supplied listener.

    Args:
        listener: Pre-bound IPv4 listening TCP socket supplied by supervisor.
        gateway: Numeric IPv4 address string (e.g. '192.168.8.1').
        port: Service port integer (1..65535).
        label: Portal display name (escaped in heading).
        backend: Synchronous JSON-compatible broker callable.
        max_workers: Concurrency worker limit (integer 1..64).

    Returns:
        CaptivePortalServer instance.
    """
    return CaptivePortalServer(
        listener=listener,
        gateway=gateway,
        port=port,
        label=label,
        backend=backend,
        max_workers=max_workers,
    )
