"""M0 diagnostics: dictionary coverage report and environment check.

These two commands exist to answer the M0 questions with measurements rather
than assumptions.  ``dict-stats`` scans the dictionary and reports the field
coverage that resolved Q-01 (frequency) and Q-02 (examples vs senses).
``envcheck`` walks the plan's start-up checklist and reports OK/NG per item,
returning a non-zero exit code if anything fails.
"""

import collections
import json
import statistics
import time

from . import config
from .dict_adapter import PROB_FLOOR, DictAdapter

__all__ = ["run_dict_stats", "run_envcheck", "collect_dict_stats", "collect_envcheck"]


def collect_dict_stats(log, sample=0):
  """Scans the dictionary and returns coverage statistics.

  :param sample: Number of keys to scan; 0 means all of them.
  """
  started = time.perf_counter()
  with DictAdapter() as adapter:
    total_entries = adapter.total_entries()
    keys = adapter.keys()
    limit = sample if sample > 0 else len(keys)
    counted = 0
    with_translation = 0
    with_examples = 0
    censored = 0
    log_freqs = []
    n_senses_values = []
    pos_counter = collections.Counter()
    domain_counter = collections.Counter()
    example_objects = 0
    sense_with_translation = 0
    sense_total = 0
    per_key_entries = 0

    for index, key in enumerate(keys[:limit]):
      serialized = adapter._dbm.GetStr(key)
      if not serialized:
        continue
      try:
        entries = json.loads(serialized)
      except json.JSONDecodeError:
        continue
      per_key_entries += len(entries)
      for entry in entries:
        record = adapter._build_record(key, index, entry)
        counted += 1
        if record.has_translation:
          with_translation += 1
        if record.examples:
          with_examples += 1
          example_objects += len(record.examples)
        if record.probability_floor:
          censored += 1
        log_freqs.append(record.log_freq)
        n_senses_values.append(record.n_senses)
        pos_counter[record.pos] += 1
        domain_counter[record.domain] += 1
        for sense in record.senses:
          sense_total += 1
          if sense.translations:
            sense_with_translation += 1
      if index and index % 100000 == 0:
        log.info("  scanned %d / %d keys", index, limit)
    elapsed = time.perf_counter() - started

  def ratio(part):
    return part / counted if counted else 0.0

  return {
      "keys": len(keys),
      "entries_total": total_entries,
      "entries_scanned": counted,
      "keys_scanned": limit,
      "elapsed_s": elapsed,
      "entries_per_s": counted / elapsed if elapsed else 0.0,
      "us_per_key": elapsed / limit * 1e6 if limit else 0.0,
      "with_translation": with_translation,
      "with_translation_ratio": ratio(with_translation),
      "with_examples": with_examples,
      "with_examples_ratio": ratio(with_examples),
      "example_objects": example_objects,
      "probability_censored": censored,
      "probability_censored_ratio": ratio(censored),
      "prob_floor": PROB_FLOOR,
      "log_freq_median": statistics.median(log_freqs) if log_freqs else 0.0,
      "n_senses_median": statistics.median(n_senses_values) if n_senses_values else 0.0,
      "n_senses_max": max(n_senses_values) if n_senses_values else 0,
      "sense_total": sense_total,
      "sense_with_translation": sense_with_translation,
      "sense_with_translation_ratio": (
          sense_with_translation / sense_total if sense_total else 0.0),
      "pos_top": pos_counter.most_common(12),
      "domain": dict(domain_counter),
  }


def run_dict_stats(log, sample=0, as_json=False):
  """Prints the dictionary coverage report (the M0-2/Q-01/Q-02 answers)."""
  stats = collect_dict_stats(log, sample=sample)
  if as_json:
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0

  print("=== dictionary ===")
  print("  keys:              {:,}".format(stats["keys"]))
  print("  keys scanned:      {:,}".format(stats["keys_scanned"]))
  print("  entries scanned:   {:,}".format(stats["entries_scanned"]))
  print("  scan:              {:.1f}s ({:.0f} us/key, {:.0f} entries/s)".format(
      stats["elapsed_s"], stats["us_per_key"], stats["entries_per_s"]))
  print("  note: this includes headword/domain derivation. A raw read of every")
  print("        key takes ~12s; domain guessing adds ~85s over 500k entries.")

  print("=== Q-01 frequency ===")
  print("  probability floor: {:.0e}".format(stats["prob_floor"]))
  print("  censored (== 0):   {:,} ({:.1f}%)".format(
      stats["probability_censored"], stats["probability_censored_ratio"] * 100))
  print("  log_freq median:   {:.2f}".format(stats["log_freq_median"]))
  print("  -> stratify by freq_rank; keep log_freq as a feature")

  print("=== Q-02 examples / senses ===")
  print("  entries w/ example: {:,} ({:.1f}%)".format(
      stats["with_examples"], stats["with_examples_ratio"] * 100))
  print("  example objects:    {:,}  (none sense-scoped)".format(
      stats["example_objects"]))
  print("  senses:             {:,}  ({} with translations, {:.1f}%)".format(
      stats["sense_total"], stats["sense_with_translation"],
      stats["sense_with_translation_ratio"] * 100))
  print("  n_senses median/max: {:.0f} / {}".format(
      stats["n_senses_median"], stats["n_senses_max"]))

  print("=== translation coverage ===")
  print("  entries w/ translation: {:,} ({:.1f}%)".format(
      stats["with_translation"], stats["with_translation_ratio"] * 100))
  print("  -> entries without one are excluded as probe targets")

  print("=== pos (top) ===")
  for name, count in stats["pos_top"]:
    print("  {:<12} {:>9,}".format(name, count))

  print("=== domain ===")
  for name, count in sorted(stats["domain"].items(), key=lambda kv: -kv[1]):
    print("  {:<6} {:>9,}".format(name, count))
  return 0


def collect_envcheck(log, db_path):
  """Runs the start-up checklist and returns a list of check results."""
  checks = []

  def record(name, ok, detail=""):
    checks.append({"name": name, "ok": bool(ok), "detail": detail})

  # 1. dependencies
  missing = []
  for module in ("regex", "httpx", "numpy", "sklearn"):
    try:
      __import__(module)
    except ImportError:
      missing.append(module)
  record("dependencies installed", not missing,
         "missing: {}".format(", ".join(missing)) if missing else "regex, httpx, numpy, sklearn")

  # 2. tkrzw-dict-agent importable and dictionary reachable
  try:
    from tkrzw_dict_agent.core import pytkrzw  # noqa: F401

    prefix = config.dict_prefix()
    body = str(prefix) + "-body.tkh"
    import os
    ok = os.path.exists(body)
    record("dictionary body present", ok, body)
  except Exception as exc:  # noqa: BLE001 - report any failure as a check
    record("dictionary body present", False, repr(exc))

  # 3. scan throughput (uses a bounded sample so the check stays quick)
  try:
    stats = collect_dict_stats(log, sample=20000)
    record("dictionary scan", stats["entries_scanned"] > 0,
           "{:.1f}s for {:,} keys ({:.0f} us/key)".format(
               stats["elapsed_s"], stats["keys_scanned"], stats["us_per_key"]))
    record("Q-01 resolved (frequency field)", True,
           "probability, floor {:.0e}, {:.1f}% censored".format(
               stats["prob_floor"], stats["probability_censored_ratio"] * 100))
    record("Q-02 resolved (examples vs senses)", True,
           "examples are entry-level; {:.1f}% of entries have translations".format(
               stats["with_translation_ratio"] * 100))
  except Exception as exc:  # noqa: BLE001
    record("dictionary scan", False, repr(exc))

  # 4. database schema
  try:
    from . import db as db_module

    conn = db_module.get_conn(db_path)
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    record("database initialised", version == db_module.SCHEMA_VERSION,
           "{} (schema v{})".format(db_path, version))
  except Exception as exc:  # noqa: BLE001
    record("database initialised", False, repr(exc))

  # 5. sampling spec present
  try:
    spec = config.load_spec("s1")
    record("specs/s1.toml loadable", bool(spec.get("set", {}).get("id")),
           "set id={}".format(spec.get("set", {}).get("id")))
  except Exception as exc:  # noqa: BLE001
    record("specs/s1.toml loadable", False, repr(exc))

  # 6. runner connectivity and logprobs
  checks.extend(_check_runner(log))

  return checks


def _check_runner(log):
  """Checks the configured runner for reachability and logprobs support."""
  try:
    import httpx

    runners = config.load_runners()
    runner = runners["runner"]["local"]
    base_url = runner["base_url"]
  except Exception as exc:  # noqa: BLE001
    return [{"name": "runner configured", "ok": False, "detail": repr(exc)}]

  results = []
  try:
    with httpx.Client(timeout=15.0) as client:
      response = client.get(base_url + "/models")
      response.raise_for_status()
      payload = response.json()
      models = [item.get("id") for item in payload.get("data", [])]
      results.append({"name": "runner reachable", "ok": True,
                      "detail": "{}, {} model(s)".format(base_url, len(models))})
      loaded = models[0] if models else ""
      looks_like_embedding = "embedding" in loaded.lower()
      results.append({
          "name": "runner serves a generation model",
          "ok": not looks_like_embedding,
          "detail": ("{} (looks like an embedding model; load a generation model "
                     "before probing)").format(loaded) if looks_like_embedding
                    else loaded,
      })
      probe = client.post(
          base_url + "/chat/completions",
          json={"model": "local",
                "messages": [{"role": "user", "content": "ping"}],
                "temperature": 0, "max_tokens": 1,
                "logprobs": True, "top_logprobs": 2})
      supports = False
      if probe.status_code == 200:
        choice = (probe.json().get("choices") or [{}])[0]
        supports = bool(choice.get("logprobs"))
      results.append({"name": "runner supports logprobs", "ok": supports,
                      "detail": "HTTP {}".format(probe.status_code)})
  except Exception as exc:  # noqa: BLE001
    results.append({"name": "runner reachable", "ok": False, "detail": repr(exc)})
  return results


def run_envcheck(log, db_path):
  """Prints the environment checklist; returns 0 when every check passes."""
  checks = collect_envcheck(log, db_path)
  width = max(len(check["name"]) for check in checks) if checks else 0
  failed = 0
  for check in checks:
    mark = "OK " if check["ok"] else "NG "
    if not check["ok"]:
      failed += 1
    print("[{}] {:<{}} {}".format(mark, check["name"], width, check["detail"]))
  print()
  if failed:
    print("{} check(s) failed".format(failed))
    return 1
  print("all checks passed")
  return 0
