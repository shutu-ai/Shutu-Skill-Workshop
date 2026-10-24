"""OpenCode adapter with explicit V1/V2 event normalization."""

from .adapter import OpenCodeAdapter

__all__ = ["OpenCodeAdapter", "create_event_sink_server"]


def __getattr__(name: str):
    if name == "create_event_sink_server":
        from .event_sink import create_event_sink_server

        return create_event_sink_server
    raise AttributeError(name)
