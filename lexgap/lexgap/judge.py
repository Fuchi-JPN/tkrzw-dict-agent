"""Judge: turns a model's answer into a verdict against the dictionary.

The judge answers one question: *did the model produce an acceptable Japanese
translation of the target word?*  It does so by normalising both sides and
comparing the answer with the entry's gloss set.

Labels follow the plan (section 4.5):

======  ==========================================================================
``K``   Known -- the normalised answer equals an accepted gloss.
``P``   Partial -- the answer and a gloss overlap (one contains the other, or the
        answer is a generalisation such as 引き締め for 金融引き締め).
``X``   eXternal -- no match, but the answer is a plausible Japanese word that the
        gloss set may simply not contain.  Needs a human or LLM audit.
``U``   Unknown -- a clear mismatch: empty, non-Japanese, or prose rather than a word.
======  ==========================================================================

Rules 3 and 4 of the plan cannot be separated by string comparison, so both
land in the audit queue and the threshold is decided from the audit results
rather than guessed here.  That is deliberate: it makes the K/P boundary a
data-driven decision.
"""

from . import db as db_module, normalizer

__all__ = ["JUDGE_VERSION", "classify", "judge_set", "queue_audit_tasks", "LABELS"]

JUDGE_VERSION = "j2"

LABELS = ("K", "P", "X", "U")

# An answer longer than this is prose, not a translation of a word.
MAX_ANSWER_CHARS = 24

# For a partial match the shorter string must be at least this fraction of the
# longer, so that 締 does not "match" 締め付け.
MIN_OVERLAP_RATIO = 0.5


class GlossResolver:
  """Widens the accepted gloss set beyond the entry's own translations.

  Measured on a 2,000-word sample, the median entry lists only four Japanese
  translations, so many correct answers are rejected simply because the entry
  does not happen to list them.  The resolver adds two escapes, both derived
  from the dictionary itself:

  * **synonym glosses** -- the glosses of headwords that share a translation
    with the target word (``Dictionary.expand_glosses``);
  * **reverse link** -- the answer is a translation of one of those synonyms.

  Validated against the 200 audited answers: recall rose from 0.376 to 0.447
  while precision stayed at 0.91, at the cost of one false acceptance
  (``drumstick`` -> もも肉).  It is a real but modest gain -- the suggestion
  that the 43.3% "present somewhere in the dictionary" figure was recoverable
  turned out to be an overestimate, because existing somewhere is not the same
  as being a translation of a synonym of the target.
  """

  def __init__(self, dictionary, synonym_limit=12, per_synonym=40, extra_limit=200):
    self._dictionary = dictionary
    self._synonym_limit = synonym_limit
    self._per_synonym = per_synonym
    self._extra_limit = extra_limit
    self._expanded = {}
    self._synonyms = {}

  def expand(self, word):
    """Normalised synonym-linked gloss set for a word (cached)."""
    if word not in self._expanded:
      raw = self._dictionary.expand_glosses(
          word, synonym_limit=self._synonym_limit,
          per_synonym=self._per_synonym, extra_limit=self._extra_limit)
      self._expanded[word] = {normalizer.normalize(g) for g in raw if g}
      self._synonyms[word] = {
          s.lower() for s in self._dictionary.synonyms(
              word, limit=self._synonym_limit)}
    return self._expanded[word]

  def synonyms(self, word):
    if word not in self._synonyms:
      self.expand(word)
    return self._synonyms[word]

  def match(self, word, answer):
    """Returns the rule name when the answer is acceptable for the word.

    Tried only after the plain rules fail, so it can never take a verdict away
    from them.  The answer is also tried in its morphological variants.
    """
    variants = normalizer.japanese_variants(answer)
    expanded = self.expand(word)
    for variant in variants:
      if variant in expanded:
        return "synonym_gloss"
    synonyms = self.synonyms(word)
    if synonyms:
      for variant in variants:
        hits = self._dictionary.search_reverse(variant, 4)
        for hit in hits:
          if (hit.get("word") or "").lower() in synonyms:
            return "synonym_reverse"
    return None


def classify(raw_output, glosses, headword=None, resolver=None):
  """Classifies one answer against a gloss set.

  :param raw_output: The model's answer, or None on a failed probe.
  :param glosses: Iterable of accepted Japanese translations.
  :param headword: The target word; required for the resolver.
  :param resolver: Optional :class:`GlossResolver`.  When given, an answer that
      the plain rules reject is retried against synonym-linked glosses and
      morphological variants, and a match is reported as ``P`` with the rule
      naming the path that matched.
  :returns: ``(label, rule, matched)``.
  """
  if raw_output is None:
    return "U", "empty_output", None
  answer = normalizer.normalize(raw_output)
  if not answer:
    return "U", "empty_output", None
  if len(answer) > MAX_ANSWER_CHARS:
    # The model answered with a sentence or explained itself.
    return "U", "too_long", None

  accepted = {}
  for gloss in glosses:
    normalized = normalizer.normalize(gloss)
    if normalized:
      accepted.setdefault(normalized, gloss)

  if answer in accepted:
    return "K", "exact", accepted[answer]

  # Partial: the answer is contained in a gloss (a generalisation, rule 3) or a
  # gloss is contained in the answer (the answer added detail).
  for normalized, original in accepted.items():
    shorter, longer = sorted((answer, normalized), key=len)
    if shorter and shorter in longer:
      if len(shorter) / len(longer) >= MIN_OVERLAP_RATIO:
        return "P", "substring", original

  # A non-Japanese answer is a wrong answer whatever the dictionary says, so
  # the resolver is only consulted for Japanese output.
  if not normalizer.has_japanese(answer):
    return "U", "unmatched_non_japanese", None

  # No string overlap.  A synonym-linked gloss or a morphological variant may
  # still match; that is a valid answer reached indirectly, so it is a P.
  if resolver is not None and headword:
    rule = resolver.match(headword, answer)
    if rule:
      return "P", rule, answer

  # Still nothing.  A plausible Japanese word may be a valid translation the
  # dictionary lacks, which is exactly what the audit decides.
  return "X", "unmatched_japanese", None


def judge_set(conn, set_id, model_id, task=None, log=None, limit=None,
              use_resolver=True):
  """Judges every unjudged probe of a sample set and stores the verdicts.

  :param use_resolver: Whether to widen the accepted gloss set with synonym
      links and morphological variants (judge version ``j2``).  Setting it to
      False reproduces the plain string matching of ``j1``.
  :returns: A counter of labels plus the number of probes considered.
  """
  sql = ("SELECT p.id, p.headword, p.raw_output FROM probes p "
         "WHERE p.set_id = ? AND p.model_id = ?")
  params = [set_id, model_id]
  if task:
    sql += " AND p.task = ?"
    params.append(task)
  sql += " ORDER BY p.id"
  rows = conn.execute(sql, params).fetchall()
  if limit:
    rows = rows[:limit]

  from .dict_adapter import DictAdapter

  counts = {label: 0 for label in LABELS}
  rules = {}
  judged = 0
  resolver = None
  parent = None
  with DictAdapter() as adapter:
    if use_resolver:
      from tkrzw_dict_agent.core import db as parent_db

      # The resolver needs the full Dictionary (reverse index included), which
      # the LexGap adapter does not expose.
      parent = parent_db.open_dictionary(adapter.prefix)
      resolver = GlossResolver(parent)
    try:
      for row in rows:
        existing = conn.execute(
            "SELECT id FROM judgments WHERE probe_id = ? AND judge_ver = ?",
            (row["id"], JUDGE_VERSION)).fetchone()
        if existing:
          continue
        glosses = adapter.gloss_set(row["headword"])
        label, rule, matched = classify(
            row["raw_output"], glosses, headword=row["headword"],
            resolver=resolver)
        with db_module.transaction(conn):
          conn.execute(
              "INSERT INTO judgments (probe_id, label, rule, matched, "
              "normalizer_ver, judge_ver, created_at) VALUES (?,?,?,?,?,?,?)",
              (row["id"], label, rule, matched, normalizer.NORMALIZER_VERSION,
               JUDGE_VERSION, db_module.utcnow()))
        counts[label] += 1
        rules[rule] = rules.get(rule, 0) + 1
        judged += 1
        if log and judged % 200 == 0:
          log.info("  judged %d", judged)
    finally:
      if parent is not None:
        parent.close()

  summary = dict(counts)
  summary["judged"] = judged
  summary["rules"] = rules
  return summary


def queue_audit_tasks(conn, set_id, model_id, per_layer=10, auditor="llm",
                      log=None):
  """Queues probes for audit, drawn evenly from the frequency layers.

  The plan asks for an initial 200 items sampled across frequency layers, so
  each quintile of the sample contributes the same number.  Only non-``K``
  verdicts are queued: a ``K`` is an exact match and carries no ambiguity.

  :returns: The number of tasks queued.
  """
  rows = conn.execute(
      "SELECT p.id AS probe_id, s.quantile, s.layer, j.label "
      "FROM probes p "
      "JOIN samples s ON s.set_id = p.set_id AND s.headword = p.headword "
      "LEFT JOIN judgments j ON j.probe_id = p.id AND j.judge_ver = ? "
      "WHERE p.set_id = ? AND p.model_id = ? AND p.raw_output IS NOT NULL "
      "ORDER BY p.id", (JUDGE_VERSION, set_id, model_id)).fetchall()

  buckets = {}
  for row in rows:
    if row["label"] == "K":
      continue
    quantile = row["quantile"]
    key = quantile if row["quantile"] is not None else 0
    buckets.setdefault(key, []).append(row["probe_id"])

  queued = 0
  with db_module.transaction(conn):
    for key in sorted(buckets):
      for probe_id in buckets[key][:per_layer]:
        conn.execute(
            "INSERT INTO audit_tasks (probe_id, auditor, status, created_at) "
            "VALUES (?,?,?,?)",
            (probe_id, auditor, "pending", db_module.utcnow()))
        queued += 1
  if log:
    log.info("queued %d audit task(s) across %d layer(s)", queued, len(buckets))
  return queued


def load_judgments(conn, set_id, model_id, task=None):
  """Loads judged rows joined with their sample layer, for reporting."""
  sql = ("SELECT p.headword, p.task, p.raw_output, p.tokens_in, p.tokens_out, "
         "j.label, j.rule, j.matched, s.quantile, s.layer, s.log_freq, "
         "s.domain FROM probes p "
         "JOIN judgments j ON j.probe_id = p.id AND j.judge_ver = ? "
         "LEFT JOIN samples s ON s.set_id = p.set_id AND s.headword = p.headword "
         "WHERE p.set_id = ? AND p.model_id = ?")
  params = [JUDGE_VERSION, set_id, model_id]
  if task:
    sql += " AND p.task = ?"
    params.append(task)
  return [dict(row) for row in conn.execute(sql, params).fetchall()]
