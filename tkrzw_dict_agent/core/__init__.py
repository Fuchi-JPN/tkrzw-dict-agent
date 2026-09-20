"""Core layer of the vocabulary sidecar.

Exposes the pure-Python dictionary reader (``pytkrzw``), the read-only
dictionary accessor (``db``), the context scorer (``scorer``), text
normalization helpers (``normalizer``) and the query engine (``query``).
"""

from . import db, normalizer, pytkrzw, query, scorer, upstream
from .db import Dictionary, DictionaryError, open_dictionary
from .query import VocabularyQuery
from .scorer import Scorer

__all__ = [
    "db",
    "normalizer",
    "pytkrzw",
    "query",
    "scorer",
    "upstream",
    "Dictionary",
    "DictionaryError",
    "open_dictionary",
    "VocabularyQuery",
    "Scorer",
]
