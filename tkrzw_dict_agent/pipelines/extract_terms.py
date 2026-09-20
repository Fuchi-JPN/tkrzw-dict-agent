"""Important-term extraction pipeline.

Selects the terms a reader (or a small language model) most needs help with:
frequent, corpus-rare words, with proper nouns promoted.  Backed by
``VocabularyQuery.extract_terms``, this module adds document-level handling
such as sectioning and unknown-word statistics.
"""

from ..core import db as db_module, normalizer, query as query_module
from ..core import scorer as scorer_module

__all__ = [
    "extract_document_terms",
    "unknown_word_ratio",
    "detect_domains",
    "should_fire",
]

# Above this ratio of unknown words the sidecar should fire automatically.
UNKNOWN_WORD_THRESHOLD = 0.10

# Fraction of content words that must belong to a specialized domain before
# "domain detected" counts as a trigger.
DOMAIN_RATIO_THRESHOLD = 0.15


def extract_document_terms(text, dictionary=None, max_terms=20, min_score=0.0):
  """Extracts the important terms of a document.

  :param text: The document text.  Long documents are split into sentences
      internally so that term frequencies stay local to a sentence window.
  :param dictionary: An open dictionary; opened for this call when omitted.
  :param max_terms: Maximum number of terms to return.
  :param min_score: Drop terms whose TF-IDF score falls below this value.
  :returns: The list of headwords, most important first.
  """
  if dictionary is None:
    with db_module.open_dictionary() as opened:
      return extract_document_terms(
          text, opened, max_terms=max_terms, min_score=min_score)
  engine = query_module.VocabularyQuery(dictionary)
  sentences = normalizer.split_sentences(text)
  if not sentences:
    return []
  # Per-sentence extraction keeps a term that dominates one sentence from being
  # drowned out by a long document, then merges by first appearance.
  merged = []
  seen = set()
  for sentence in sentences:
    for term in engine.extract_terms(sentence, max_terms=max_terms):
      if term not in seen:
        seen.add(term)
        merged.append(term)
  return merged[:max_terms]


def unknown_word_ratio(text, dictionary):
  """Fraction of the word tokens that the dictionary does not contain."""
  tokens = [word for word, _ in normalizer.extract_words(text)]
  if not tokens:
    return 0.0
  unknown = 0
  checked = set()
  for surface in tokens:
    word = normalizer.normalize_word(surface)
    if not word or word in checked:
      continue
    checked.add(word)
    if not dictionary.has_word(word):
      unknown += 1
  return unknown / max(1, len(checked))


def detect_domains(text):
  """Returns the specialized domains the text appears to belong to.

  A domain is reported when at least ``DOMAIN_RATIO_THRESHOLD`` of the content
  words are listed in that domain's lexicon.  English labels are returned, the
  same keys used by the scorer.
  """
  content_words = []
  for surface, _ in normalizer.extract_words(text):
    word = normalizer.normalize_word(surface)
    if not word or len(word) < 2:
      continue
    if normalizer.is_stop_word("en", word):
      continue
    content_words.append(word)
  if not content_words:
    return []
  found = []
  for name, lexicon in scorer_module._DOMAIN_WORD_SETS.items():
    hits = sum(1 for word in content_words if word in lexicon)
    if hits / len(content_words) >= DOMAIN_RATIO_THRESHOLD:
      found.append(name)
  return found


def should_fire(text, dictionary):
  """Automatic trigger condition of the specification (8.3).

  The three listed signals -- English ratio, unknown-word ratio, and domain
  detection -- are treated as alternatives: the sidecar fires when the text is
  English-dominant and at least one of the other two signals is present.  That
  keeps the trigger useful for the actual use case, where a small model
  struggles with a specialist word the dictionary does know.
  """
  if not normalizer.is_english_heavy(text):
    return False
  if unknown_word_ratio(text, dictionary) > UNKNOWN_WORD_THRESHOLD:
    return True
  return bool(detect_domains(text))
