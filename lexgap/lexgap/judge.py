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

JUDGE_VERSION = "j1"

LABELS = ("K", "P", "X", "U")

# An answer longer than this is prose, not a translation of a word.
MAX_ANSWER_CHARS = 24

# For a partial match the shorter string must be at least this fraction of the
# longer, so that 締 does not "match" 締め付け.
MIN_OVERLAP_RATIO = 0.5


def classify(raw_output, glosses):
  """Classifies one answer against a gloss set.

  :param raw_output: The model's answer, or None on a failed probe.
  :param glosses: Iterable of accepted Japanese translations.
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

  # No string overlap.  A plausible Japanese word may still be a valid
  # translation the dictionary lacks, which is exactly what the audit decides.
  if normalizer.has_japanese(answer):
    return "X", "unmatched_japanese", None
  return "U", "unmatched_non_japanese", None


def judge_set(conn, set_id, model_id, task=None, log=None, limit=None):
  """Judges every unjudged probe of a sample set and stores the verdicts.

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
  judged = 0
  with DictAdapter() as adapter:
    for row in rows:
      existing = conn.execute(
          "SELECT id FROM judgments WHERE probe_id = ? AND judge_ver = ?",
          (row["id"], JUDGE_VERSION)).fetchone()
      if existing:
        continue
      glosses = adapter.gloss_set(row["headword"])
      label, rule, matched = classify(row["raw_output"], glosses)
      with db_module.transaction(conn):
        conn.execute(
            "INSERT INTO judgments (probe_id, label, rule, matched, "
            "normalizer_ver, judge_ver, created_at) VALUES (?,?,?,?,?,?,?)",
            (row["id"], label, rule, matched, normalizer.NORMALIZER_VERSION,
             JUDGE_VERSION, db_module.utcnow()))
      counts[label] += 1
      judged += 1
      if log and judged % 200 == 0:
        log.info("  judged %d", judged)

  summary = dict(counts)
  summary["judged"] = judged
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
