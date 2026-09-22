"""Read-only adapter over the tkrzw-dict union dictionary.

LexGap never touches the dictionary directly; it goes through this module, so
the dictionary's quirks are described in exactly one place.  Three of those
quirks were established by the M0 investigation and shape the API:

**Q-01 -- the frequency field is left-censored.**
``probability`` is present on 100% of the 501,069 entries, but it has a
reporting floor of ``1e-07``: 38% of entries record ``0`` because they fall
below the threshold.  Those entries are not spread evenly -- they are the
bottom 30% by frequency rank (100% of ranks 70-100%, 0% of ranks 0-60%).
The plain-text key list ``union-keys.txt`` *is* frequency-ordered and covers
every word, so the adapter exposes both:

* ``prob``      -- the raw probability, possibly 0 (left-censored).
* ``freq_rank`` -- 0-based rank in the key list, full coverage.

Stratified sampling therefore uses ``freq_rank`` percentiles, while
``log_freq`` (censored, from ``prob``) stays available as a predictor feature.

**Q-02 -- examples are entry-level, not sense-level.**
Of 1,186,371 example objects, none carries a marker tying it to a specific
sense.  Sense-scoped gating (condition C4) therefore cannot rely on examples;
the plan's fallback applies and the top-scoring sense is used instead.

**Translation coverage.**
``translation`` is present on 85.8% of entries.  The missing 14.2% have no
recoverable Japanese gloss (their sense texts carry no ``[translation]:``
either), so they are excluded as probe targets -- there is no ground truth to
judge against.

Reading ``union-keys.txt`` rather than walking hash buckets is deliberate: the
file has exactly as many lines as the database has records (499,750), in
frequency order, which gives iteration order for free.
"""

import io
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config

__all__ = [
  "DictAdapter",
  "HeadwordRecord",
  "Sense",
  "PROB_FLOOR",
  "SENSE_TRANSLATION_PATTERN",
]

# The observed reporting floor of `probability` (Q-01).  Values below it are
# stored as 0, so log-frequency is censored here rather than undefined.
PROB_FLOOR = 1e-07

# Sense translations live inside the sense text as "[translation]: a, b, c"
# and run until the next bracketed marker or the end of the string.
SENSE_TRANSLATION_PATTERN = re.compile(
    r"\[translation\]:\s*(.*?)(?=\[(?:-|synonym|antonym|hypernym|hyponym|"
    r"derivative|synset|example|note|usage|etymology)|\Z)",
    re.DOTALL)

# Position tags used by the dictionary; the first sense usually carries the
# most representative one.
_DEFAULT_POS = "unkpos"


@dataclass
class Sense:
  """One sense (``item``) of an entry."""

  pos: str
  text: str
  label: str = ""
  translations: list = field(default_factory=list)

  def to_dict(self):
    return {
        "pos": self.pos,
        "text": self.text,
        "label": self.label,
        "translations": list(self.translations),
    }


@dataclass
class HeadwordRecord:
  """A normalised view of one dictionary entry.

  This is the shape the sampler, feature builder and judge consume, so it is
  intentionally flat and JSON-serialisable.
  """

  key: str
  word: str
  freq_rank: int
  prob: float
  log_freq: float
  pos: str
  pos_list: list
  n_senses: int
  translations: list
  senses: list
  examples: list
  related: list
  cooccurrence: list
  domain: str
  pronunciation: str = ""
  probability_floor: bool = False

  @property
  def has_translation(self):
    return bool(self.translations)

  @property
  def n_examples(self):
    return len(self.examples)

  def to_dict(self):
    return {
        "key": self.key,
        "word": self.word,
        "freq_rank": self.freq_rank,
        "prob": self.prob,
        "log_freq": self.log_freq,
        "pos": self.pos,
        "pos_list": list(self.pos_list),
        "n_senses": self.n_senses,
        "translations": list(self.translations),
        "senses": [sense.to_dict() for sense in self.senses],
        "examples": list(self.examples),
        "related": list(self.related),
        "cooccurrence": list(self.cooccurrence),
        "domain": self.domain,
        "pronunciation": self.pronunciation,
        "probability_floor": self.probability_floor,
    }


class DictAdapter:
  """Read-only access to the union dictionary.

  The three ``.tkh`` databases are memory-mapped by the tkrzw-dict-agent
  reader; this adapter additionally reads the key list and parses entries into
  :class:`HeadwordRecord`.
  """

  def __init__(self, prefix=None, keys_path=None):
    from tkrzw_dict_agent.core import pytkrzw, scorer

    self._pytkrzw = pytkrzw
    self._scorer = scorer
    self._prefix = Path(prefix) if prefix else config.dict_prefix()
    self._keys_path = Path(keys_path) if keys_path else Path(
        str(self._prefix) + "-keys.txt")
    self._body_path = Path(str(self._prefix) + "-body.tkh")
    if not self._body_path.exists():
      raise FileNotFoundError(
          "dictionary body not found: {} (run scripts/setup.sh)".format(self._body_path))
    if not self._keys_path.exists():
      raise FileNotFoundError(
          "key list not found: {}".format(self._keys_path))
    self._dbm = pytkrzw.DBM()
    self._dbm.Open(str(self._body_path), False, dbm="HashDBM").OrDie()
    self._keys = None
    self._rank_cache = None

  # -- lifecycle ---------------------------------------------------------
  def close(self):
    if self._dbm is not None:
      self._dbm.Close()
      self._dbm = None

  def __enter__(self):
    return self

  def __exit__(self, exc_type, exc, tb):
    self.close()
    return False

  @property
  def prefix(self):
    return self._prefix

  # -- iteration ---------------------------------------------------------
  def keys(self):
    """Returns the headword keys in frequency order (cached)."""
    if self._keys is None:
      self._keys = [line.rstrip("\n")
                    for line in io.open(self._keys_path, encoding="utf-8")
                    if line.strip()]
    return self._keys

  def total_entries(self):
    """Number of dictionary entries (not keys; a key may hold several)."""
    return self._dbm.Count()

  def iter_headwords(self, require_translation=False, ascii_only=False):
    """Yields every entry as a :class:`HeadwordRecord`, most frequent first.

    :param require_translation: skip entries with no Japanese gloss.  Needed
        for probes, which have no ground truth without a translation.
    :param ascii_only: skip headwords that are not plain ASCII letters or
        hyphen, matching the sampler's ``^[A-Za-z-]+$`` filter.
    """
    keys = self.keys()
    ascii_pattern = re.compile(r"^[A-Za-z-]+$")
    for rank, key in enumerate(keys):
      serialized = self._dbm.GetStr(key)
      if not serialized:
        continue
      try:
        entries = json.loads(serialized)
      except json.JSONDecodeError:
        continue
      for entry in entries:
        word = entry.get("word") or key
        if ascii_only and not ascii_pattern.match(word):
          continue
        record = self._build_record(key, rank, entry)
        if require_translation and not record.has_translation:
          continue
        yield record

  def lookup(self, word):
    """Exact lookup of one normalized key.

    :returns: A list of :class:`HeadwordRecord` (a key can hold several
        entries, e.g. ``japan`` -> ``Japan`` and ``japan``), or an empty list.
    """
    key = self._normalize_key(word)
    serialized = self._dbm.GetStr(key)
    if not serialized:
      return []
    try:
      entries = json.loads(serialized)
    except json.JSONDecodeError:
      return []
    rank = self._rank_of(key)
    return [self._build_record(key, rank, entry) for entry in entries]

  def lookup_one(self, word):
    """First match of :meth:`lookup`, or None."""
    matches = self.lookup(word)
    return matches[0] if matches else None

  def gloss_set(self, word):
    """Every Japanese translation of a word, across all of its senses.

    Used by the judge to decide whether a model's output is acceptable.  The
    result is the union of the entry-level ``translation`` list and every
    sense's ``[translation]:`` list.
    """
    glosses = set()
    for record in self.lookup(word):
      glosses.update(record.translations)
      for sense in record.senses:
        glosses.update(sense.translations)
    return {gloss for gloss in glosses if gloss}

  def stats(self):
    """Coverage statistics used by the M0 environment check."""
    keys = self.keys()
    return {
        "keys": len(keys),
        "entries": self.total_entries(),
        "keys_path": str(self._keys_path),
        "body_path": str(self._body_path),
    }

  # -- internals ---------------------------------------------------------
  def _normalize_key(self, word):
    from tkrzw_dict_agent.core import normalizer

    return normalizer.normalize_word(word)

  def _rank_of(self, key):
    if self._rank_cache is None:
      self._rank_cache = {value: index for index, value in enumerate(self.keys())}
    return self._rank_cache.get(key, -1)

  def _build_record(self, key, rank, entry):
    word = entry.get("word") or key
    senses = self._parse_senses(entry)
    translations = [value for value in (entry.get("translation") or []) if value]
    if not translations:
      # Fall back to whatever the senses carry, in order.
      seen = set()
      for sense in senses:
        for value in sense.translations:
          if value not in seen:
            seen.add(value)
            translations.append(value)
    pos_list = []
    for sense in senses:
      if sense.pos and sense.pos not in pos_list:
        pos_list.append(sense.pos)
    raw_prob = _safe_float(entry.get("probability"))
    censored = raw_prob <= 0.0
    effective_prob = PROB_FLOOR if censored else raw_prob
    examples = []
    for example in entry.get("example") or []:
      if isinstance(example, dict) and example.get("e"):
        examples.append({"e": example.get("e"), "j": example.get("j", "")})
    return HeadwordRecord(
        key=key,
        word=word,
        freq_rank=rank,
        prob=raw_prob,
        log_freq=math.log10(effective_prob),
        pos=pos_list[0] if pos_list else _DEFAULT_POS,
        pos_list=pos_list,
        n_senses=len(senses),
        translations=translations,
        senses=senses,
        examples=examples,
        related=[value for value in (entry.get("related") or []) if value],
        cooccurrence=[value for value in (entry.get("cooccurrence") or []) if value],
        domain=self._guess_domain(entry),
        pronunciation=entry.get("pronunciation") or "",
        probability_floor=censored,
    )

  def _parse_senses(self, entry):
    senses = []
    for item in entry.get("item") or []:
      if not isinstance(item, dict):
        continue
      text = item.get("text") or ""
      match = SENSE_TRANSLATION_PATTERN.search(text)
      translations = []
      if match:
        translations = [value.strip()
                        for value in match.group(1).split(",")
                        if value.strip()]
      senses.append(Sense(
          pos=item.get("pos") or _DEFAULT_POS,
          text=text,
          label=item.get("label") or "",
          translations=translations))
    return senses

  def _guess_domain(self, entry):
    """Domain label from the entry's headword and sense definitions."""
    try:
      return self._scorer.guess_domain(entry)
    except Exception:
      return "一般"


def _safe_float(value):
  if value is None:
    return 0.0
  if isinstance(value, (int, float)):
    return float(value)
  try:
    return float(str(value))
  except ValueError:
    return 0.0
