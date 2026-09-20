"""Implementation of the ``vocabulary_support`` tool.

The handler is deliberately free of any transport concern: it takes the tool
arguments and returns a plain dictionary, so the same code serves the CLI, the
HTTP router, and an in-process agent runtime.  The tool definition itself lives
in ``tool_schema.json`` next to this module.
"""

import json
import os

from ..core import db as db_module, normalizer, query as query_module
from ..pipelines import enrich as enrich_pipeline
from ..pipelines import extract_terms as extract_pipeline

__all__ = [
    "VocabularySupportHandler",
    "load_tool_schema",
    "TOOL_NAME",
]

TOOL_NAME = "vocabulary_support"

_SCHEMA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tool_schema.json")


def load_tool_schema():
  """Returns the tool definition as a dictionary."""
  with open(_SCHEMA_PATH, encoding="utf-8") as schema_file:
    return json.load(schema_file)


class VocabularySupportHandler:
  """Answers calls to the ``vocabulary_support`` tool."""

  def __init__(self, dictionary=None):
    """Creates a handler.

    :param dictionary: An open dictionary to reuse.  When omitted, the
        handler opens the shipped union dictionary lazily on first use and
        keeps it for subsequent calls.
    """
    self._dictionary = dictionary
    self._owns_dictionary = dictionary is None
    self._query = None

  def _ensure_query(self):
    if self._query is None:
      if self._dictionary is None:
        self._dictionary = db_module.open_dictionary()
      self._query = query_module.VocabularyQuery(self._dictionary)
    return self._query

  def close(self):
    """Closes a dictionary this handler opened itself."""
    if self._owns_dictionary and self._dictionary is not None:
      self._dictionary.close()
      self._dictionary = None
      self._query = None

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, tb):
    self.close()
    return False

  def handle(self, arguments):
    """Runs a tool call.

    :param arguments: The tool arguments, as parsed from the model's function
        call.  ``text`` is required; ``word`` restricts the call to a single
        word; ``max_terms`` bounds the number of words explained.
    :returns: A dictionary with the resolved ``terms`` and a rendered
        ``hint`` block ready to paste into a prompt.
    """
    if not isinstance(arguments, dict):
      raise TypeError("tool arguments must be a dictionary")
    text = arguments.get("text")
    if not text or not isinstance(text, str):
      raise ValueError("the 'text' argument is required and must be a string")
    word = arguments.get("word")
    max_terms = _clamp_int(arguments.get("max_terms"), default=20, low=1, high=100)
    query = self._ensure_query()
    if word:
      meanings = query.resolve(word, context=text, max_meanings=max_terms)
      terms = [{
          "word": word,
          "candidates": [_render_candidate(m) for m in meanings],
      }]
    else:
      enrich = enrich_pipeline.enrich_text(text, self._dictionary, max_terms=max_terms)
      terms = enrich.terms
      if not terms:
        return {
            "terms": [],
            "hint": "",
            "message": "no dictionary-covered terms found in the text",
        }
    enrichment = enrich_pipeline.Enrichment(text, terms)
    return {
        "terms": terms,
        "hint": enrich_pipeline.format_vocabulary_hints(enrichment),
    }

  def handle_json(self, arguments_json):
    """Runs a tool call from a raw JSON argument string."""
    if isinstance(arguments_json, (bytes, bytearray)):
      arguments_json = arguments_json.decode("utf-8")
    return self.handle(json.loads(arguments_json))


def _render_candidate(meaning):
  """Formats one meaning the way the specification's output block does."""
  domain = meaning.get("domain")
  if domain:
    return "{}（{}）".format(meaning["ja"], domain)
  return meaning["ja"]


def _clamp_int(value, default, low, high):
  if value is None:
    return default
  try:
    value = int(value)
  except (TypeError, ValueError):
    return default
  return max(low, min(high, value))
