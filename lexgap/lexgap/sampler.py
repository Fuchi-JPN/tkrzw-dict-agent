"""Sampler: builds a reproducible, frequency-stratified word sample.

S1 has three layers:

* **main** -- 20 frequency quantiles x 100 words.  The quantiles are cut on the
  frequency *rank* from ``union-keys.txt``, not on ``log(probability)``, because
  that probability is left-censored at 1e-07 and 38% of entries sit on the
  floor (M0 Q-01).  Ranking keeps the bottom of the dictionary resolvable.
* **aux_multi_sense** -- words with many senses, where a model has more room to
  pick the wrong one.
* **aux_domain** -- a per-domain slice, so a domain effect can be seen
  independently of frequency.

Determinism is the point: the spec is frozen into ``sample_sets.spec_json``,
the ordering is the dictionary's own frequency order, and every draw uses one
``random.Random(seed)``.  Re-running with the same spec must produce an
identical sample (acceptance test AC4).
"""

import json
import random

from . import db as db_module
from .dict_adapter import DictAdapter

__all__ = ["generate", "plan_quantiles", "SampleError", "MAIN_LAYER", "MULTI_SENSE_LAYER", "DOMAIN_LAYER"]

MAIN_LAYER = "main"
MULTI_SENSE_LAYER = "aux_multi_sense"
DOMAIN_LAYER = "aux_domain"


class SampleError(RuntimeError):
  """Raised when a sampling spec cannot be satisfied."""


def plan_quantiles(ranks, n_quantiles):
  """Assigns each rank to a quantile index in ``[0, n_quantiles)``.

  Quantile edges are taken from the *rank* of the whole eligible population,
  so every quantile holds a similar number of candidates.  The last quantile
  receives the remainder when the population does not divide evenly.
  """
  if not ranks:
    return {}
  ordered = sorted(ranks)
  total = len(ordered)
  edges = []
  for index in range(1, n_quantiles):
    position = index * total // n_quantiles
    edges.append(ordered[position])
  mapping = {}
  for rank in ranks:
    quantile = 0
    for edge in edges:
      if rank >= edge:
        quantile += 1
      else:
        break
    mapping[rank] = min(quantile, n_quantiles - 1)
  return mapping


def generate(conn, spec, log, limit=None):
  """Builds a sample set from a spec and writes it to the database.

  :param conn: Open database connection.
  :param spec: Parsed spec dictionary (see ``specs/s1.toml``).
  :param log: Logger.
  :param limit: Optional cap on scanned keys, for smoke runs.
  :returns: A summary dictionary.
  """
  set_id = spec["set"]["id"]
  seed = int(spec["set"]["seed"])
  spec_ver = spec["set"].get("spec_ver", "unknown")
  main_spec = spec["main"]
  aux_spec = spec.get("aux", {})
  n_quantiles = int(main_spec["n_quantiles"])
  per_quantile = int(main_spec["per_quantile"])
  require_translation = bool(main_spec.get("require_translation", True))
  multi_sense_min = int(aux_spec.get("multi_sense_min", 5))
  multi_sense_n = int(aux_spec.get("multi_sense_n", 0))
  domain_n = int(aux_spec.get("domain_n", 0))

  existing = conn.execute(
      "SELECT set_id FROM sample_sets WHERE set_id = ?", (set_id,)).fetchone()
  if existing:
    raise SampleError(
        "sample set {} already exists; use a new set id or delete it first".format(set_id))

  log.info("scanning the dictionary for set %s", set_id)
  candidates = []
  excluded_no_translation = 0
  scanned = 0
  with DictAdapter() as adapter:
    for record in adapter.iter_headwords(ascii_only=True):
      scanned += 1
      if limit is not None and scanned > limit:
        break
      if require_translation and not record.has_translation:
        excluded_no_translation += 1
        continue
      candidates.append(record)

  if not candidates:
    raise SampleError("no eligible words; check the spec filters")
  # A dictionary key can hold several entries and two of them can share a
  # display word once case is folded ("Japan" and "japan").  The samples table
  # is unique on (set_id, headword, layer), so fold the duplicates here and keep
  # the first, which is the entry the frequency order put first.
  deduped_candidates = []
  seen_headwords = set()
  for record in candidates:
    key = record.word.lower()
    if key in seen_headwords:
      continue
    seen_headwords.add(key)
    deduped_candidates.append(record)
  duplicate_entries = len(candidates) - len(deduped_candidates)
  candidates = deduped_candidates
  log.info("eligible words: %d (scanned %d, %d without a translation, "
           "%d folded duplicates)", len(candidates), scanned,
           excluded_no_translation, duplicate_entries)

  ranks = [record.freq_rank for record in candidates]
  quantile_of_rank = plan_quantiles(ranks, n_quantiles)

  # Group by quantile, preserving frequency order within each group so that a
  # draw is reproducible regardless of dict iteration details.
  by_quantile = {index: [] for index in range(n_quantiles)}
  for record in candidates:
    by_quantile[quantile_of_rank[record.freq_rank]].append(record)

  rng = random.Random(seed)
  rows = []
  quantile_sizes = {}
  for quantile in range(n_quantiles):
    group = by_quantile[quantile]
    quantile_sizes[quantile] = len(group)
    take = min(per_quantile, len(group))
    for record in rng.sample(group, take):
      rows.append(_row(set_id, record, MAIN_LAYER, quantile=quantile))

  # Auxiliary layer: many-sense words, which give the model more chances to
  # choose the wrong sense.
  if multi_sense_n:
    pool = [record for record in candidates if record.n_senses >= multi_sense_min]
    take = min(multi_sense_n, len(pool))
    for record in rng.sample(pool, take):
      rows.append(_row(set_id, record, MULTI_SENSE_LAYER))

  # Auxiliary layer: per-domain slices.
  if domain_n:
    by_domain = {}
    for record in candidates:
      by_domain.setdefault(record.domain, []).append(record)
    for domain in sorted(by_domain):
      pool = by_domain[domain]
      take = min(domain_n, len(pool))
      for record in rng.sample(pool, take):
        rows.append(_row(set_id, record, DOMAIN_LAYER, domain=domain))

  # The plan allows a word to appear in both the main and an auxiliary layer,
  # provided the overlap is visible.  Auxiliary rows that duplicate a main-layer
  # headword are kept and flagged with is_duplicate=1, so an analysis can drop
  # them or weight them down without losing the membership information.
  main_headwords = {row["headword"] for row in rows if row["layer"] == MAIN_LAYER}
  for row in rows:
    if row["layer"] != MAIN_LAYER and row["headword"] in main_headwords:
      row["is_duplicate"] = 1

  main_count = sum(1 for row in rows if row["layer"] == MAIN_LAYER)
  with db_module.transaction(conn):
    conn.execute(
        "INSERT INTO sample_sets (set_id, spec_json, seed, created_at, note) "
        "VALUES (?,?,?,?,?)",
        (set_id, json.dumps(spec, ensure_ascii=False), seed, db_module.utcnow(),
         main_spec.get("note") or spec_ver))
    conn.executemany(
        "INSERT INTO samples (set_id, headword, layer, quantile, log_freq, "
        "n_senses, domain, is_duplicate, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [(row["set_id"], row["headword"], row["layer"], row["quantile"],
          row["log_freq"], row["n_senses"], row["domain"], row["is_duplicate"],
          db_module.utcnow()) for row in rows])

  summary = {
      "set_id": set_id,
      "seed": seed,
      "spec_ver": spec_ver,
      "scanned": scanned,
      "eligible": len(candidates),
      "excluded_no_translation": excluded_no_translation,
      "main": main_count,
      "multi_sense": sum(1 for row in rows if row["layer"] == MULTI_SENSE_LAYER),
      "domain": sum(1 for row in rows if row["layer"] == DOMAIN_LAYER),
      "total": len(rows),
      "quantile_sizes": quantile_sizes,
      "quantile_fill": {
          index: min(per_quantile, size) for index, size in quantile_sizes.items()},
  }
  return summary


def _row(set_id, record, layer, quantile=None, domain=None):
  return {
      "set_id": set_id,
      "headword": record.word.lower(),
      "layer": layer,
      "quantile": quantile,
      "log_freq": record.log_freq,
      "n_senses": record.n_senses,
      "domain": domain or record.domain,
      "is_duplicate": 0,
  }
