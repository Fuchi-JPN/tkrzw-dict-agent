"""Agent integration surface for the vocabulary sidecar.

Exports the ``vocabulary_support`` tool: its JSON definition, a transport-free
handler, an optional FastAPI router (``POST /lookup``, ``/enrich``,
``/extract``) and the ``dict-server`` entry point that binds ``0.0.0.0:8765``
so other hosts can call it.
"""

from .handler import (
    TOOL_NAME,
    VocabularySupportHandler,
    load_tool_schema,
)

__all__ = [
    "TOOL_NAME",
    "VocabularySupportHandler",
    "load_tool_schema",
    "build_router",
    "create_app",
    "run_server",
]


def build_router(dictionary=None):
  """Lazily builds the FastAPI router (requires the ``fastapi`` extra)."""
  from .router import build_router as _build_router

  return _build_router(dictionary)


def create_app(dictionary=None):
  """Lazily creates the stand-alone FastAPI application."""
  from .router import create_app as _create_app

  return _create_app(dictionary)


def run_server(argv=None):
  """Runs the HTTP API under uvicorn (requires the ``api`` extra)."""
  from .server import main as _main

  return _main(argv)

