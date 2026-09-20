"""Dictionary access layer for the vocabulary sidecar.

Wraps the vendored ``UnionSearcher`` behind a small, typed interface that the
agent API and pipelines use: single-word lookup, inflection resolution,
context-aware search, and whole-text annotation.  All data access is
read-only.
"""

import os

from . import normalizer, upstream

__all__ = ["Dictionary", "DictionaryError", "open_dictionary"]

DEFAULT_CAPACITY = 100


class DictionaryError(RuntimeError):
  """Raised when the dictionary cannot be opened or queried."""


class Dictionary:
  """Read-only accessor for the union English-Japanese dictionary.

  A single instance owns one ``UnionSearcher``, which in turn keeps the three
  ``.tkh`` databases and the key lists memory-mapped.  Instances are cheap to
  create but the underlying mappings take a moment to open, so long-lived
  services should reuse one instance.
  """

  def __init__(self, data_prefix, capacity=DEFAULT_CAPACITY):
    """Prepares the searcher without opening the databases yet.

    :param data_prefix: Path prefix of the dictionary data, e.g.
      ``.../tkrzw-dict/union``.  The suffixes ``-body.tkh``,
      ``-tran-index.tkh``, ``-infl-index.tkh`` and ``-keys.txt`` are appended.
    :param capacity: Default maximum number of entries a search returns.
    """
    if not data_prefix:
      raise DictionaryError("a data prefix is required")
    self._data_prefix = str(data_prefix)
    self._capacity = int(capacity)
    self._searcher = None
    self._open = False

  def __enter__(self):
    self.open()
    return self

  def __exit__(self, exc_type, exc, tb):
    self.close()
    return False

  @property
  def data_prefix(self):
    return self._data_prefix

  def open(self):
    """Opens the underlying databases.

    The union searcher maps the three ``.tkh`` files on construction, so a bad
    data prefix raises here.  The plain-text key lists are opened
    opportunistically: they are only needed by the pattern search modes, and
    lookups keep working without them.
    """
    if self._open:
      return
    try:
      searcher = upstream.UnionSearcher(self._data_prefix)
      searcher.OpenKeysFile()
    except Exception as exc:
      raise DictionaryError(
          "cannot open dictionary at {}: {}".format(self._data_prefix, exc))
    self._searcher = searcher
    self._open = True

  def close(self):
    """Releases the underlying databases."""
    if not self._open:
      return
    self._searcher = None
    self._open = False

  def is_open(self):
    return self._open

  def lookup(self, word):
    """Exact lookup of a word, resolving inflections when needed.

    Returns the list of matching entries, or an empty list when the word is
    unknown.  Each entry is the JSON record stored in the dictionary body:
    ``word``, ``item``, ``translation``, ``related``, ``cooccurrence``,
    ``probability`` and friends.
    """
    self._ensure_open()
    entries = self._searcher.SearchExact(word, self._capacity)
    return entries or []

  def has_word(self, word):
    """Cheap existence check that avoids deserializing the record."""
    self._ensure_open()
    return bool(self._searcher.CheckExact(normalizer.normalize_word(word)))

  def lookup_raw(self, word):
    """Returns the raw JSON string stored for a normalized key, or None."""
    self._ensure_open()
    return self._searcher.SearchBody(normalizer.normalize_word(word))

  def resolve_inflection(self, word):
    """Maps an inflected form to its possible lemmas.

    ``ran`` -> ``["run"]``, ``mice`` -> ``["mouse"]``.  Returns an empty list
    when the form is already a lemma or is unknown.
    """
    self._ensure_open()
    return self._searcher.SearchInflections(normalizer.normalize_word(word)) or []

  def lookup_with_inflection(self, word):
    """Looks up a word, falling back to its lemmas on a miss."""
    entries = self.lookup(word)
    if entries:
      return entries
    lemmas = self.resolve_inflection(word)
    for lemma in lemmas:
      entries = self.lookup(lemma)
      if entries:
        return entries
    return []

  def search_context(self, core, prefix, suffix, capacity=None):
    """Searches for phrases matching a core word with surrounding context.

    Mirrors ``UnionSearcher.SearchWithContext``: the core must be present, and
    the prefix and suffix narrow the match to the given context.
    """
    self._ensure_open()
    capacity = capacity if capacity is not None else self._capacity
    return self._searcher.SearchWithContext(
        normalizer.normalize_word(core),
        normalizer.normalize_word(prefix),
        normalizer.normalize_word(suffix),
        capacity) or []

  def search_reverse(self, japanese, capacity=None):
    """Reverse lookup: Japanese words back to English headwords."""
    self._ensure_open()
    capacity = capacity if capacity is not None else self._capacity
    return self._searcher.SearchExactReverse(japanese, capacity) or []

  def annotate(self, text):
    """Annotates an English text with dictionary hits.

    Yields ``(span_text, is_word, entries)`` triples covering the whole text,
    exactly like ``UnionSearcher.AnnotateText`` but with the non-word spans
    passed through untouched.
    """
    self._ensure_open()
    return self._searcher.AnnotateText(text)

  def word_probability(self, entry):
    """Returns the corpus probability of an entry as a float."""
    probability = entry.get("probability") if isinstance(entry, dict) else None
    return _parse_probability(probability)

  def _ensure_open(self):
    if not self._open:
      raise DictionaryError("dictionary is not open; call open() first")


def open_dictionary(data_prefix=None, capacity=DEFAULT_CAPACITY):
  """Opens and returns a :class:`Dictionary`.

    When ``data_prefix`` is omitted, the union dictionary that ships with the
    vendored ``tkrzw-dict`` checkout is used.
  """
  if data_prefix is None:
    data_prefix = _default_data_prefix()
  dictionary = Dictionary(data_prefix, capacity=capacity)
  dictionary.open()
  return dictionary


def _default_data_prefix():
  """Resolves the data prefix from the environment or the vendored checkout.

  ``TKRZW_DICT_PREFIX`` names the prefix directly (e.g. ``/data/union``);
  ``TKRZW_DICT_DIR`` names the directory that holds ``union-*``.  The explicit
  prefix wins, so the CLI and the API server agree on one variable.
  """
  explicit_prefix = os.environ.get("TKRZW_DICT_PREFIX")
  if explicit_prefix:
    return explicit_prefix
  dict_dir = upstream.get_dict_dir()
  return "{}/union".format(dict_dir.rstrip("/"))


def _parse_probability(value):
  """Parses the probability field, which is stored as a string like '.00002'."""
  if value is None:
    return 0.0
  if isinstance(value, (int, float)):
    return float(value)
  try:
    return float(str(value))
  except ValueError:
    return 0.0


def serialize_entry(entry, limit=None):
  """Returns a compact JSON representation of an entry for API responses."""
  compact = {
      "word": entry.get("word"),
      "translation": entry.get("translation", [])[:limit or 20],
      "related": entry.get("related", [])[:limit or 10],
  }
  pronunciation = entry.get("pronunciation")
  if pronunciation:
    compact["pronunciation"] = pronunciation
  compact["probability"] = _parse_probability(entry.get("probability"))
  return compact
