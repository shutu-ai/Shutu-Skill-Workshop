"""Read-only Workshop MCP entry points for the native plugin."""

__all__ = ["TOOLS", "WorkshopMcpService", "handle_request", "run_stdio"]


def __getattr__(name: str):
    if name == "WorkshopMcpService":
        from .service import WorkshopMcpService

        return WorkshopMcpService
    if name in {"TOOLS", "handle_request", "run_stdio"}:
        from .server import TOOLS, handle_request, run_stdio

        return {"TOOLS": TOOLS, "handle_request": handle_request, "run_stdio": run_stdio}[name]
    raise AttributeError(name)
