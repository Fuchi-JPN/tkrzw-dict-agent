"""Scoring of candidate meanings.

Implements the scoring policy of the specification:

    score = base_frequency + domain_match + context_similarity

Each component is normalized to ``[0, 1]`` and weighted, then the weighted
sum is returned as the meaning score.  The weights are exposed as module
constants so the behaviour can be retuned without touching call sites.
"""

import functools
import math

import regex

from . import normalizer

__all__ = ["Scorer", "guess_domain"]

# Component weights.  They sum to 1.0 so that a meaning score is always in
# [0, 1]; context similarity is the strongest signal, frequency is a prior.
WEIGHT_BASE_FREQUENCY = 0.30
WEIGHT_DOMAIN_MATCH = 0.30
WEIGHT_CONTEXT_SIMILARITY = 0.40

# Probabilities in the corpus range from about 1e-2 (the most common words)
# down to 1e-7 (the rarest entries).  Mapping that log range onto [0, 1]
# gives a stable frequency prior.
_PROB_LOG_FLOOR = -7.0
_PROB_LOG_SPAN = 5.0

# Cooccurrence and related-word slice widths used as domain evidence.
_DOMAIN_EVIDENCE_LIMIT = 24

# Domain labelling scans the headword and the first sense definitions.  A
# headword hit weighs more than a gloss word, and a domain needs a minimum
# score so that a single incidental gloss word does not win.
_HEADWORD_HIT_WEIGHT = 3
_DOMAIN_ITEM_LIMIT = 12
_DOMAIN_TEXT_LIMIT = 400
_MIN_DOMAIN_SCORE = 2

# Feature-model decay, mirroring the vendored engine's GetFeatures.
_FEATURE_DECAY = 0.95
_FEATURE_RELATED_LIMIT = 16
_FEATURE_COOCCURRENCE_LIMIT = 20

# Small lexicons used only to attach a human-readable domain label to the
# output.  ``domain_match`` itself is computed from corpus cooccurrence and
# does not depend on these lists.
_DOMAIN_LEXICON = {
    "economics": (
        "economics economy economic finance financial market markets money "
        "bank banking banks interest rate rates inflation deflation currency "
        "monetary fiscal policy tax taxes budget debt trade investment "
        "stock stocks bond bonds credit loan loans capital profit loss "
        "recession growth GDP central bank fed currency exchange price "
        "supply demand contraction tightening easing"),
    "medicine": (
        "medicine medical health healthcare doctor doctors patient patients "
        "hospital clinic disease diseases illness illness diagnosis "
        "treatment therapy surgery drug drugs medicine dose clinical "
        "symptom symptoms infection virus bacteria cancer vaccine immune "
        "nurse nursing physician prescription chronic acute anatomy cell "
        "gene genetic epidemic pandemic recovery rehabilitation"),
    "technology": (
        "technology computer computers software hardware network networks "
        "internet database programming algorithm server servers client cloud "
        "processor semiconductor robotics automation electronics electronic "
        "compiler encryption bandwidth firmware middleware virtualization "
        "microprocessor operating system computer program source code"),
    "law": (
        "law laws legal lawyer lawyers court courts judge justice "
        "legislation statute regulations contract contracts rights "
        "crime crimes criminal case cases trial appeal attorney "
        "constitutional parliament congress senate amendment liability "
        "property lawsuit prosecution evidence verdict jurisdiction"),
    "science": (
        "science scientific research experiment experiments theory "
        "hypothesis physics chemistry biology laboratory molecule "
        "reaction element particle energy force motion quantum cell "
        "organism species evolution ecosystem climate measurement "
        "observation analysis data discovery telescope microscope"),
}

# Japanese labels for the output format of the specification: （経済） etc.
_DOMAIN_LABELS = {
    "economics": "経済",
    "medicine": "医療",
    "technology": "IT",
    "law": "法律",
    "science": "科学",
    "general": "一般",
}


class Scorer:
  """Scores candidate meanings for a word in context.

  A scorer is stateless apart from the corpus statistics it consults through
  the dictionary, so one instance can be shared across requests.
  """

  def __init__(self, dictionary):
    self._dictionary = dictionary

  def base_frequency(self, entry):
    """Frequency prior of the entry, in [0, 1].  Common words score higher."""
    probability = self._dictionary.word_probability(entry)
    if probability <= 0:
      return 0.0
    log_prob = math.log10(probability)
    value = (log_prob - _PROB_LOG_FLOOR) / _PROB_LOG_SPAN
    return max(0.0, min(1.0, value))

  def domain_match(self, entry, context_words):
    """Overlap between the context and the entry's cooccurrence evidence.

    The corpus cooccurrence list of an entry is the strongest available
    signal of the domain a sense belongs to: when the surrounding words also
    appear in that list, the sense is likely the intended one.  Returns a
    value in [0, 1].
    """
    if not context_words:
      return 0.0
    evidence = self._domain_evidence(entry)
    if not evidence:
      return 0.0
    context_set = set(context_words)
    overlap = len(evidence & context_set)
    if overlap == 0:
      return 0.0
    # Saturating score: two shared domain words already indicate a strong
    # match, and more evidence adds diminishing returns.
    return min(1.0, 0.4 * overlap + 0.2)

  def _domain_evidence(self, entry):
    evidence = set()
    cooccurrence = entry.get("cooccurrence") or []
    for word in cooccurrence[:_DOMAIN_EVIDENCE_LIMIT]:
      normalized = _fast_normalize(word)
      if normalized:
        evidence.add(normalized)
    related = entry.get("related") or []
    for word in related[:_DOMAIN_EVIDENCE_LIMIT]:
      normalized = _fast_normalize(word)
      if normalized:
        evidence.add(normalized)
    return evidence

  def context_similarity(self, entry, context_features):
    """Cosine similarity between the entry and the context feature vectors.

    ``context_features`` is a mapping of normalized word to weight, typically
    built from the surrounding sentence.  The entry features are built with a
    lightweight normalizer: the exact upstream normalizer spends a regular
    expression per character, which is fine for lookups but far too slow for
    the per-term scoring a batch enrich performs.
    """
    if not context_features:
      return 0.0
    entry_features = build_entry_features(entry)
    if not entry_features:
      return 0.0
    return _cosine_similarity(context_features, entry_features)

  def score_meaning(self, entry, context_features, context_words):
    """Combines the three components into a single score in [0, 1]."""
    base = self.base_frequency(entry)
    domain = self.domain_match(entry, context_words)
    similarity = self.context_similarity(entry, context_features)
    return (
        WEIGHT_BASE_FREQUENCY * base +
        WEIGHT_DOMAIN_MATCH * domain +
        WEIGHT_CONTEXT_SIMILARITY * similarity)

  def rank_entries(self, entries, context_features, context_words, limit=None):
    """Returns ``(entry, score)`` pairs sorted by descending score."""
    scored = []
    for entry in entries:
      score = self.score_meaning(entry, context_features, context_words)
      scored.append((entry, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    if limit is not None:
      scored = scored[:limit]
    return scored


_RE_WHITESPACE = regex.compile(r"\s+")
_RE_MIDDLE_DOTS = regex.compile(r"[・·]+")


@functools.lru_cache(maxsize=65536)
def fast_normalize(text):
  """Cheap normalization for feature vectors and lexicon matching.

  Lower-cases, collapses whitespace and drops middle dots.  Diacritics are
  left intact: they do not affect similarity enough to justify a Unicode
  decomposition per word.
  """
  if not text:
    return ""
  lowered = text.lower()
  return _RE_WHITESPACE.sub(" ", _RE_MIDDLE_DOTS.sub("", lowered)).strip()


# Backwards-compatible alias for the internal name used inside this module.
_fast_normalize = fast_normalize


# The lexicon is matched with the fast normalizer so that lookup keys and
# lexicon entries agree.
_DOMAIN_WORD_SETS = {
    name: frozenset(_fast_normalize(word) for word in words.split())
    for name, words in _DOMAIN_LEXICON.items()
}


def guess_domain(entry, context_words=None):
  """Returns the Japanese domain label that best fits an entry.

  Used for the output format of the specification, e.g. ``金融引き締め（経済）``.

  The evidence is the headword and the English sense definitions, not the
  ``related``/``cooccurrence`` lists: those are broad and pull in unrelated
  domains (a "policy" entry co-occurs with legal words, but its definition is
  generic).  A headword hit alone is strong; gloss hits need at least two
  before they outweigh the default.  Falls back to ``一般``.
  """
  scores = {}
  headword = _fast_normalize(entry.get("word") or "")
  if headword:
    for name, lexicon in _DOMAIN_WORD_SETS.items():
      if headword in lexicon:
        scores[name] = scores.get(name, 0) + _HEADWORD_HIT_WEIGHT
  for item in (entry.get("item") or [])[:_DOMAIN_ITEM_LIMIT]:
    text = item.get("text") or ""
    if not text:
      continue
    for surface, _ in normalizer.extract_words(text[:_DOMAIN_TEXT_LIMIT]):
      word = _fast_normalize(surface)
      if not word:
        continue
      for name, lexicon in _DOMAIN_WORD_SETS.items():
        if word in lexicon:
          scores[name] = scores.get(name, 0) + 1
  if not scores:
    return _DOMAIN_LABELS["general"]
  best_name, best_score = max(scores.items(), key=lambda pair: (pair[1], pair[0]))
  if best_score < _MIN_DOMAIN_SCORE:
    return _DOMAIN_LABELS["general"]
  return _DOMAIN_LABELS[best_name]


def build_entry_features(entry):
  """Builds a lightweight feature vector for an entry.

  Models the vendored engine's GetFeatures -- headword, related words and
  cooccurrence words with a decaying weight -- but with the fast normalizer,
  which is roughly two orders of magnitude cheaper for a batch enrich.
  """
  features = {}
  headword = _fast_normalize(entry.get("word") or "")
  if headword:
    features[headword] = 1.0
  score = 1.0
  related = entry.get("related") or []
  for word in related[:_FEATURE_RELATED_LIMIT]:
    normalized = _fast_normalize(word)
    if normalized and normalized not in features:
      score *= _FEATURE_DECAY
      features[normalized] = score
  score = max(score, 0.4)
  cooccurrence = entry.get("cooccurrence") or []
  for word in cooccurrence[:_FEATURE_COOCCURRENCE_LIMIT]:
    normalized = _fast_normalize(word)
    if normalized and normalized not in features:
      score *= _FEATURE_DECAY
      features[normalized] = score
  return features


def _cosine_similarity(features_a, features_b):
  """Cosine similarity of two sparse feature vectors."""
  if not features_a or not features_b:
    return 0.0
  if len(features_a) > len(features_b):
    features_a, features_b = features_b, features_a
  product = 0.0
  for word, weight in features_a.items():
    other = features_b.get(word)
    if other:
      product += weight * other
  if product <= 0:
    return 0.0
  norm_a = math.sqrt(sum(weight * weight for weight in features_a.values()))
  norm_b = math.sqrt(sum(weight * weight for weight in features_b.values()))
  if norm_a == 0 or norm_b == 0:
    return 0.0
  return min(product / (norm_a * norm_b), 1.0)
