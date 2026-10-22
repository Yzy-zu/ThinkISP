"""Errores públicos sin importar herramientas ni módulos específicos de Linux."""


class ThinkISPError(RuntimeError):
    """Error operativo que CLI y TUI pueden presentar sin traceback."""
