"""Tkrzw-Dict Python for AI Agents.

A read-only, pure-Python vocabulary sidecar that gives an agent Japanese
candidate meanings for the difficult English words of a text.  It is built on
the ``tkrzw-dict`` union dictionary.  No C++ extension is required: the Tkrzw
``HashDBM`` files are read directly by :mod:`tkrzw_dict_agent.core.pytkrzw`.

Typical use::

    from tkrzw_dict_agent import VocabularySidecar

    sidecar = VocabularySidecar()
    sidecar.lookup("tightening", "The Fed's tightening policy")
    sidecar.enrich("The Fed's tightening monetary policy surprised markets.")
    sidecar.close()

The tool entry point for an agent runtime is
:class:`tkrzw_dict_agent.agent_api.VocabularySupportHandler`.
"""

from .core import Dictionary, VocabularyQuery, open_dictionary
from .core.query import VocabularyQuery as _VocabularyQuery

__version__ = "0.1.0"

__all__ = ["VocabularySidecar", "Dictionary", "VocabularyQuery", "open_dictionary"]


class VocabularySidecar:
  """Convenience facade combining an open dictionary and the query engine.

  This is the object most callers want: it opens the shipped dictionary once
  and exposes the four operations of the specification -- lookup, resolve,
  enrich and extract_terms.  Use it as a context manager, or call
  :meth:`close` when done.
  """

  def __init__(self, data_prefix=None):
    self._dictionary = open_dictionary(data_prefix)
    self._query = _VocabularyQuery(self._dictionary)

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, tb):
    self.close()
    return False

  @property
  def dictionary(self):
    return self._dictionary

  def close(self):
    """Closes the underlying dictionary."""
    self._dictionary.close()

  def lookup(self, word):
    """Exact lookup of a word; see :meth:`VocabularyQuery.lookup`."""
    return self._query.lookup(word)

  def resolve(self, word, context=""):
    """Context-ranked meanings of a word."""
    return self._query.resolve(word, context)

  def enrich(self, text):
    """Japanese candidates for the difficult words of a text."""
    return self._query.enrich(text)

  def extract_terms(self, text):
    """Important terms of a text, most important first."""
    return self._query.extract_terms(text)
