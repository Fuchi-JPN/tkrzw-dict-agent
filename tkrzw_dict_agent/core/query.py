"""Query engine: the four core operations of the vocabulary sidecar.

Implements section 6 of the specification:

- :meth:`VocabularyQuery.lookup`        exact word lookup
- :meth:`VocabularyQuery.resolve`       context-aware meaning ranking
- :meth:`VocabularyQuery.enrich`        batch candidate extraction for a text
- :meth:`VocabularyQuery.extract_terms` important-term extraction (TF-IDF)

Every operation is read-only and works on the pre-built dictionary data.
"""

import collections
import math

from . import normalizer, scorer as scorer_module

__all__ = ["VocabularyQuery", "build_context_features"]

# How many meanings a resolve() call returns by default.
DEFAULT_MAX_MEANINGS = 8

# How many terms enrich()/extract_terms() return by default.
DEFAULT_MAX_TERMS = 20

# Translations inside an entry are ordered by corpus frequency.  Each later
# translation is discounted slightly so an early, frequent sense wins ties.
TRANSLATION_ORDER_DECAY = 0.94

# How the frequency prior and the context signal combine when ordering the
# translations of an entry.  The context must be able to override the prior:
# in "the river bank" the river sense is third in frequency order yet is the
# right one, so context carries the larger share.
TRANSLATION_PRIOR_WEIGHT = 0.4
TRANSLATION_CONTEXT_WEIGHT = 0.6

# Multiplier applied to proper nouns (capitalized mid-sentence) when ranking
# extracted terms, per the "named entities first" policy of the spec.
PROPER_NOUN_BONUS = 1.5

# Stop words and very short tokens never count as important terms.
_MIN_TERM_LENGTH = 2

# How much of a sense gloss is scanned when linking translations to senses.
_SENSE_TEXT_LIMIT = 320

# Weight given to related words pulled in to expand the context.
_EXPANSION_WEIGHT = 0.5


class VocabularyQuery:
  """The agent-facing vocabulary operations, backed by one dictionary."""

  def __init__(self, dictionary, scorer=None):
    self._dictionary = dictionary
    self._scorer = scorer or scorer_module.Scorer(dictionary)
    # The dictionary is read-only, so per-entry derived data is stable and can
    # be cached across calls.  Both caches are keyed by headword and bounded.
    self._sense_cache = _BoundedCache(2048)
    self._domain_cache = _BoundedCache(2048)

  @property
  def dictionary(self):
    return self._dictionary

  def lookup(self, word):
    """Exact lookup of a word, resolving inflections on a miss.

    Returns a compact record:

    .. code-block:: python

        {
            "word": "tightening",
            "found": True,
            "pronunciation": "/ˈtaɪtənɪŋ/",
            "translations": ["締め付け", "引き締め", ...],
            "related": ["contraction", "restriction", ...],
            "probability": 1.85e-05,
            "lemmas": [],
        }

    ``found`` is False and the lists are empty when the word is unknown.
    """
    lemmas = []
    entries = self._dictionary.lookup(word)
    if not entries:
      lemmas = self._dictionary.resolve_inflection(word)
      for lemma in lemmas:
        entries = self._dictionary.lookup(lemma)
        if entries:
          break
    if not entries:
      return {
          "word": word,
          "found": False,
          "pronunciation": "",
          "translations": [],
          "related": [],
          "probability": 0.0,
          "lemmas": lemmas,
      }
    entry = entries[0]
    return {
        "word": entry.get("word", word),
        "found": True,
        "pronunciation": entry.get("pronunciation", ""),
        "translations": list(entry.get("translation") or []),
        "related": list(entry.get("related") or []),
        "probability": self._dictionary.word_probability(entry),
        "lemmas": lemmas,
    }

  def resolve(self, word, context="", max_meanings=DEFAULT_MAX_MEANINGS):
    """Ranks the Japanese meanings of a word within a context.

    Returns a list of meaning records ordered by descending score:

    .. code-block:: python

        {"ja": "金融引き締め", "domain": "経済", "score": 0.92}

    An empty list means the word is not in the dictionary.
    """
    entries = self._dictionary.lookup_with_inflection(word)
    if not entries:
      return []
    # The word being resolved must not expand the context, or every sense of
    # it would look relevant.
    exclude = {normalizer.normalize_word(word)}
    exclude.update(normalizer.normalize_word(lemma)
                   for lemma in self._dictionary.resolve_inflection(word))
    context_features = build_context_features(
        self._dictionary, context, exclude=exclude)
    context_words = sorted(context_features.keys())
    return self._rank_meanings(
        entries, context_features, context_words, max_meanings)

  def _rank_meanings(self, entries, context_features, context_words,
                     max_meanings):
    """Turns ranked entries into the meaning records of the specification.

    Two signals order the meanings: the entry score ranks whole entries against
    the context, and a per-meaning boost reorders the translations inside an
    entry by looking at which sense definition mentions each translation.  The
    second signal matters for words whose record bundles several unrelated
    senses, e.g. "Fed" (central bank / animal feed).
    """
    ranked = self._scorer.rank_entries(
        entries, context_features, context_words, limit=max(1, max_meanings))
    meanings = []
    for entry, entry_score in ranked:
      translations = entry.get("translation") or []
      if not translations:
        continue
      domain = self._entry_domain(entry)
      sense_vectors = self._entry_sense_vectors(entry)
      translations = translations[:max_meanings]
      similarities = [
          _translation_context_similarity(t, sense_vectors, context_features)
          for t in translations
      ]
      max_similarity = max(similarities) if similarities else 0.0
      scored_translations = []
      order = 1.0
      for translation, similarity in zip(translations, similarities):
        if max_similarity > 0:
          # Relative context score: the best-matching sense gets 1.0, so the
          # combination below is comparable across entries.
          weight = (TRANSLATION_PRIOR_WEIGHT * order +
                    TRANSLATION_CONTEXT_WEIGHT * (similarity / max_similarity))
        else:
          weight = order
        scored_translations.append((translation, weight))
        order *= TRANSLATION_ORDER_DECAY
      scored_translations.sort(key=lambda pair: pair[1], reverse=True)
      for translation, weight in scored_translations:
        score = max(0.0, min(1.0, entry_score * weight))
        meanings.append({
            "ja": translation,
            "domain": domain,
            "score": round(score, 4),
            "word": entry.get("word"),
        })
        if len(meanings) >= max_meanings:
          break
      if len(meanings) >= max_meanings:
        break
    return meanings

  def enrich(self, text, max_terms=DEFAULT_MAX_TERMS):
    """Extracts every dictionary-covered term of a text with its candidates.

    Returns the batch record of the specification:

    .. code-block:: python

        {"terms": [{"word": "tightening",
                    "candidates": ["金融引き締め", "強化"]}]}

    Function words are skipped: a vocabulary sidecar exists to explain
    difficult words, not to gloss "the" and "of".  Words the dictionary does
    not contain are skipped silently.
    """
    if not text or not text.strip():
      return {"terms": []}
    # Collect the candidate terms first: the words being explained must not
    # expand the shared context, or each would make all of its own senses look
    # relevant.  The annotation itself does not need the context features.
    candidates = []
    seen = set()
    exclude = set()
    for span, is_word, entries in self._dictionary.annotate(text):
      if not is_word or not entries:
        continue
      surface = span.strip()
      if not surface or surface in seen:
        continue
      if normalizer.is_stop_word("en", surface):
        continue
      seen.add(surface)
      candidates.append((surface, entries))
      exclude.add(normalizer.normalize_word(surface))
      if len(candidates) >= max_terms:
        break
    if not candidates:
      return {"terms": []}
    # The context features are shared by every term, so they are built once
    # here instead of inside resolve(); that keeps enrich at a few milliseconds.
    context_features = build_context_features(
        self._dictionary, text, exclude=exclude)
    context_words = sorted(context_features.keys())
    terms = []
    for surface, entries in candidates:
      meanings = self._rank_meanings(
          entries, context_features, context_words, DEFAULT_MAX_MEANINGS)
      if not meanings:
        continue
      terms.append({
          "word": surface,
          "candidates": [meaning["ja"] for meaning in meanings],
          "meanings": meanings,
      })
    return {"terms": terms}

  def extract_terms(self, text, max_terms=DEFAULT_MAX_TERMS):
    """Selects the important terms of a text by TF-IDF.

    Named entities (capitalized words that are not sentence-initial) are
    boosted, and function words are excluded.  Returns the headwords ordered
    by descending importance.
    """
    if not text or not text.strip():
      return []
    surfaces = normalizer.extract_words(text)
    if not surfaces:
      return []
    counts = {}
    proper = set()
    sentence_start = True
    for surface, _ in surfaces:
      normalized = normalizer.normalize_word(surface)
      if not normalized or len(normalized) < _MIN_TERM_LENGTH:
        sentence_start = False
        continue
      if normalizer.is_stop_word("en", normalized):
        sentence_start = False
        continue
      if normalizer.is_numeric_word(normalized):
        sentence_start = False
        continue
      counts[normalized] = counts.get(normalized, 0) + 1
      if not sentence_start and surface[0].isupper():
        proper.add(normalized)
      sentence_start = False
    if not counts:
      return []
    scored = []
    total = sum(counts.values())
    for word, tf in counts.items():
      weight = tf / total
      if word in proper:
        weight *= PROPER_NOUN_BONUS
      idf = self._inverse_document_frequency(word)
      if idf is None:
        continue
      scored.append((word, weight * idf))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [word for word, _ in scored[:max_terms]]

  def _entry_domain(self, entry):
    """Domain label of an entry, cached by headword.

    The entry's own related and cooccurrence evidence determines the label, so
    it does not depend on the surrounding context and can be cached safely.
    """
    key = entry.get("word") or ""
    cached = self._domain_cache.get(key)
    if cached is None:
      cached = scorer_module.guess_domain(entry)
      self._domain_cache.put(key, cached)
    return cached

  def _entry_sense_vectors(self, entry):
    """Sense feature vectors of an entry, cached by headword."""
    key = entry.get("word") or ""
    cached = self._sense_cache.get(key)
    if cached is None:
      cached = _sense_vectors(entry)
      self._sense_cache.put(key, cached)
    return cached

  def _inverse_document_frequency(self, word):
    """IDF derived from the corpus probability stored in the dictionary.

    Returns None when the word is unknown to the dictionary.  Rare words get a
    high value, common words a low one.
    """
    entries = self._dictionary.lookup(word)
    if not entries:
      return None
    probability = self._dictionary.word_probability(entries[0])
    if probability <= 0:
      return None
    # Map the log probability onto an IDF-like scale.  A word at the floor of
    # the corpus range scores highest; the most frequent words score near zero.
    idf = (math.log10(probability) - scorer_module._PROB_LOG_FLOOR) / 2.0
    return max(0.0, min(1.0, idf))


class _BoundedCache:
  """A tiny insertion-ordered cache with a fixed capacity.

  ``functools.lru_cache`` cannot be used directly because the cached values are
  keyed by dictionary records, which are unhashable; entries are keyed by their
  headword instead.
  """

  __slots__ = ("_capacity", "_data")

  def __init__(self, capacity):
    self._capacity = capacity
    self._data = collections.OrderedDict()

  def get(self, key):
    return self._data.get(key)

  def put(self, key, value):
    data = self._data
    data[key] = value
    data.move_to_end(key)
    if len(data) > self._capacity:
      data.popitem(last=False)


def build_context_features(dictionary, context, max_words=64, expand=True,
                           expansion_seeds=8, expansion_terms=8, exclude=None):
  """Builds a feature vector for a context string.

  Each dictionary-known word of the context contributes its term frequency.
  When ``expand`` is set, the first few content words are additionally expanded
  with their dictionary ``related`` words at half weight.  That expansion is
  what makes sense disambiguation work: a context saying "the river bank" only
  matches the river sense of "bank" once "river" brings in "riverbank",
  "riverside" and "watercourse", all of which appear in that sense definition.

  ``exclude`` names words that must not be expanded.  The word under
  disambiguation belongs there: expanding "bank" would pull in the related
  words of *every* sense and make all senses look equally relevant.
  """
  if not context:
    return {}
  exclude = exclude or frozenset()
  base = collections.OrderedDict()
  for surface, _ in normalizer.extract_words(context):
    word = normalizer.normalize_word(surface)
    if not word or len(word) < _MIN_TERM_LENGTH:
      continue
    if normalizer.is_stop_word("en", word):
      continue
    if word not in base:
      if not dictionary.has_word(word):
        continue
      base[word] = 0.0
    base[word] += 1.0
  features = dict(base)
  if expand and base:
    seeds = [word for word in base if word not in exclude][:expansion_seeds]
    stop = False
    for seed in seeds:
      entries = dictionary.lookup(seed)
      if not entries:
        continue
      related = (entries[0].get("related") or [])[:expansion_terms]
      for related_word in related:
        word = scorer_module.fast_normalize(related_word)
        if word and word not in features:
          features[word] = _EXPANSION_WEIGHT
        if len(features) >= max_words:
          stop = True
          break
      if stop:
        break
  return features


def _sense_vectors(entry):
  """Builds one feature vector per sense definition of an entry.

  Each ``item`` carries an English gloss; indexing the gloss words locally
  lets a translation be tied back to the sense that mentions it.  Only the
  head of each gloss is scanned, and normalization is the cheap variant: an
  entry can carry a dozen WordNet glosses and this runs per term.
  """
  vectors = []
  for item in entry.get("item") or []:
    text = item.get("text") or ""
    if not text:
      continue
    head = text[:_SENSE_TEXT_LIMIT]
    words = set()
    for surface, _ in normalizer.extract_words(head):
      word = scorer_module.fast_normalize(surface)
      if word and len(word) >= _MIN_TERM_LENGTH:
        words.add(word)
    if words:
      vectors.append((head, words))
  return vectors


def _translation_context_similarity(translation, sense_vectors, context_features):
  """Cosine-like similarity in [0, 1] between a translation's sense and a context.

  When a sense gloss mentions the translation, the gloss is compared with the
  context.  Normalising by the gloss length matters: an entry like "bank"
  carries glosses of very different sizes, and a raw overlap count would let
  the longest gloss win every time.  Translations that cannot be tied to a
  sense score zero.
  """
  if not context_features or not sense_vectors:
    return 0.0
  context_norm = math.sqrt(
      sum(weight * weight for weight in context_features.values()))
  if context_norm <= 0:
    return 0.0
  best = 0.0
  for text, terms in sense_vectors:
    if translation not in text:
      continue
    overlap = 0.0
    for word in terms:
      weight = context_features.get(word)
      if weight:
        overlap += weight
    if overlap <= 0:
      continue
    similarity = overlap / (context_norm * math.sqrt(len(terms)))
    best = max(best, min(1.0, similarity))
  return best
