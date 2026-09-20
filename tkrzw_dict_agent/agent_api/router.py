"""HTTP API for the vocabulary sidecar.

Exposes the three endpoints of the specification (section 11)::

    POST /lookup
    POST /enrich
    POST /extract

The router is optional: FastAPI is only imported when this module is used, so
the core library has no web dependency.  Mount it on an application with::

    from tkrzw_dict_agent.agent_api import router
    app.include_router(router.build_router())
"""

from ..core import db as db_module, query as query_module

__all__ = ["build_router", "create_app", "RouterState"]

DEFAULT_MAX_MEANINGS = 8
DEFAULT_MAX_TERMS = 20
MAX_TEXT_LENGTH = 20000


class RouterState:
  """Holds the shared dictionary and query engine for the router."""

  def __init__(self, dictionary=None):
    self._dictionary = dictionary
    self._owns_dictionary = dictionary is None
    self._query = None

  def query(self):
    if self._query is None:
      if self._dictionary is None:
        self._dictionary = db_module.open_dictionary()
      self._query = query_module.VocabularyQuery(self._dictionary)
    return self._query

  def close(self):
    if self._owns_dictionary and self._dictionary is not None:
      self._dictionary.close()
      self._dictionary = None
      self._query = None


def build_router(dictionary=None):
  """Builds and returns a FastAPI ``APIRouter`` for the sidecar.

  :param dictionary: An optional open dictionary to share with the caller.
  """
  from fastapi import APIRouter, Body, HTTPException
  from fastapi.responses import JSONResponse

  class UTF8JSONResponse(JSONResponse):
    """JSON response that states the encoding explicitly.

    JSON is UTF-8 by definition, but a client that reads the raw bytes and
    guesses the encoding (PowerShell's ``[Console]::OutputEncoding``, some
    HTTP libraries) does better when the header says so.  Japanese output is
    the normal case here, so the charset is worth declaring.
    """

    media_type = "application/json; charset=utf-8"

  state = RouterState(dictionary)
  router = APIRouter(tags=["vocabulary"], default_response_class=UTF8JSONResponse)

  @router.post("/lookup")
  def lookup(payload: dict = Body(...)):
    word = _require_text(payload, "word")
    engine = state.query()
    result = engine.lookup(word)
    meanings = engine.resolve(word, context=payload.get("context", ""),
                              max_meanings=DEFAULT_MAX_MEANINGS)
    result["meanings"] = meanings
    return result

  @router.post("/enrich")
  def enrich(payload: dict = Body(...)):
    text = _require_text(payload, "text")
    engine = state.query()
    return engine.enrich(text, max_terms=DEFAULT_MAX_TERMS)

  @router.post("/extract")
  def extract(payload: dict = Body(...)):
    text = _require_text(payload, "text")
    engine = state.query()
    return {"terms": engine.extract_terms(text, max_terms=DEFAULT_MAX_TERMS)}

  @router.get("/health")
  def health():
    return {"status": "ok", "tool": _tool_name()}

  def _require_text(payload, field):
    if not isinstance(payload, dict):
      raise HTTPException(status_code=400, detail="a JSON object body is required")
    value = payload.get(field)
    if not value or not isinstance(value, str):
      raise HTTPException(
          status_code=400, detail="'{}' is required and must be a string".format(field))
    if len(value) > MAX_TEXT_LENGTH:
      raise HTTPException(status_code=413, detail="text is too long")
    return value

  return router


def create_app(dictionary=None):
  """Creates a stand-alone FastAPI application with the sidecar mounted."""
  from fastapi import FastAPI

  app = FastAPI(title="Tkrzw-Dict Vocabulary Sidecar", version="0.1.0")
  app.include_router(build_router(dictionary))
  return app


def _tool_name():
  from .handler import TOOL_NAME
  return TOOL_NAME
