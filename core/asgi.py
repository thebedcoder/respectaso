import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

_django_app = get_asgi_application()
_mcp_asgi_app = None


def _get_mcp_app():
    global _mcp_asgi_app
    if _mcp_asgi_app is None:
        from aso.mcp_server import mcp
        _mcp_asgi_app = mcp.streamable_http_app()
    return _mcp_asgi_app


async def _handle_lifespan(scope, receive, send):
    """Initialize MCP session manager and handle ASGI lifespan protocol."""
    _get_mcp_app()  # ensures streamable_http_app() ran and created the session manager
    from aso.mcp_server import mcp as _mcp
    async with _mcp._session_manager.run():
        await receive()  # consume "lifespan.startup"
        await send({"type": "lifespan.startup.complete"})
        await receive()  # block until "lifespan.shutdown"
        await send({"type": "lifespan.shutdown.complete"})


async def application(scope, receive, send):
    if scope["type"] == "lifespan":
        await _handle_lifespan(scope, receive, send)
    elif scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
        await _get_mcp_app()(scope, receive, send)
    else:
        await _django_app(scope, receive, send)
