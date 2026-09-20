"""Batch enrichment pipeline: turn raw text into vocabulary hints.

This is the operation an agent calls when it wants Japanese candidates for the
difficult words of a passage.  It is the text-level counterpart of
``VocabularyQuery.enrich``: the query engine does the work, and this module
shapes the result into the representation the agent consumes.
"""

from ..core import db as db_module, normalizer, query as query_module

__all__ = ["enrich_text", "format_vocabulary_hints", "Enrichment"]


class Enrichment:
  """The result of enriching a text."""

  def __init__(self, text, terms):
    self.text = text
    self.terms = terms

  @property
  def words(self):
    return [term["word"] for term in self.terms]

  def candidates(self, word):
    for term in self.terms:
      if term["word"] == word:
        return list(term["candidates"])
    return []

  def to_dict(self):
    return {"text": self.text, "terms": self.terms}


def enrich_text(text, dictionary=None, max_terms=20):
  """Enriches a text with Japanese candidates for its difficult words.

  :param text: The English text to enrich.
  :param dictionary: An open :class:`~tkrzw_dict_agent.core.db.Dictionary`.
      When omitted, the shipped union dictionary is opened for this call.
  :param max_terms: Maximum number of terms to return.
  :returns: An :class:`Enrichment`.
  """
  if dictionary is None:
    with db_module.open_dictionary() as opened:
      return enrich_text(text, opened, max_terms=max_terms)
  engine = query_module.VocabularyQuery(dictionary)
  return Enrichment(text, engine.enrich(text, max_terms=max_terms)["terms"])


def format_vocabulary_hints(enrichment, max_candidates=8):
  """Renders an enrichment as the hint block of the specification.

  .. code-block:: text

      [参考語彙]
      tightening:
      - 金融引き締め（経済）
      - 強化（一般）

  The format deliberately offers candidates, never a single translation: the
  agent picks the sense that fits its context.
  """
  lines = ["[参考語彙]"]
  for term in enrichment.terms:
    lines.append("{}:".format(term["word"]))
    meanings = term.get("meanings")
    if meanings:
      for meaning in meanings[:max_candidates]:
        domain = meaning.get("domain")
        if domain:
          lines.append("- {}（{}）".format(meaning["ja"], domain))
        else:
          lines.append("- {}".format(meaning["ja"]))
    else:
      for candidate in term["candidates"][:max_candidates]:
        lines.append("- {}".format(candidate))
  return "\n".join(lines)


def should_enrich(text):
  """Decides whether a text is worth enriching (specification 8.3).

  The sidecar fires when the text is English-dominant.  The unknown-word
  threshold is evaluated against the dictionary by the caller, since it
  requires an open database.
  """
  return normalizer.is_english_heavy(text)
