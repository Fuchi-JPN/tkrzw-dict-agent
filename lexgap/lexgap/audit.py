"""Audit: an independent check on the rule-based judge.

The judge compares strings, so it cannot tell a wrong answer from a correct
translation that the gloss set happens not to contain.  The audit settles that
by asking a model -- with the gloss set, the context and the answer in front of
it -- whether the answer is acceptable.

Two numbers come out of it:

* **agreement / judge precision** -- of the answers the judge accepted (K/P),
  how many the auditor also accepts.
* **recovery** -- of the answers the judge rejected (X/U), how many the auditor
  accepts.  These are the judge's false negatives, and ``X`` is where they are
  expected to concentrate.

Audit calls are cached by content hash, so a re-run never pays twice for the
same question.
"""

import hashlib
import json

from . import config, db as db_module
from .dict_adapter import DictAdapter

__all__ = [
  "AUDIT_VERSION",
  "AUDIT_PROMPT",
  "build_audit_prompt",
  "parse_verdict",
  "run_audit",
  "summary",
]

AUDIT_VERSION = "a1"
AUDIT_PROMPT = "judge_audit_v1.txt"

ACCEPT = "accept"
REJECT = "reject"


def build_audit_prompt(word, glosses, output, context=None):
  """Renders the auditor prompt from the versioned template."""
  template = config.load_prompt(AUDIT_PROMPT)
  return template.format(
      word=word,
      glosses=", ".join(sorted(glosses)[:20]) or "(none)",
      context=context or "(none)",
      output=output or "(empty)")


def parse_verdict(text):
  """Maps the auditor's answer to accept/reject/unknown.

  The template asks for a bare はい or いいえ, so anything else is recorded as
  unknown rather than guessed at.
  """
  if not text:
    return "unknown"
  stripped = text.strip()
  lowered = stripped.lower()
  for token in ("はい", "yes", "accept"):
    if lowered.startswith(token):
      return ACCEPT
  for token in ("いいえ", "no", "reject"):
    if lowered.startswith(token):
      return REJECT
  if "はい" in stripped:
    return ACCEPT
  if "いいえ" in stripped:
    return REJECT
  return "unknown"


def _cache_key(probe_id, word, output, model_id):
  payload = "{}|{}|{}|{}|{}".format(
      probe_id, word, output or "", model_id, AUDIT_VERSION)
  return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def run_audit(conn, runner, log=None, limit=None, model_id=None):
  """Runs every pending audit task through the auditor model.

  The auditor is the same runner by default; the plan leaves the choice of a
  stronger judge model to Q-05, and recording the model id keeps the result
  interpretable either way.

  :returns: Counts of accept/reject/unknown.
  """
  auditor_model = model_id or runner.model_id
  pending = conn.execute(
      "SELECT at.id AS task_id, at.probe_id, p.headword, p.raw_output, "
      "p.parsed_json FROM audit_tasks at "
      "JOIN probes p ON p.id = at.probe_id "
      "WHERE at.status = 'pending' ORDER BY at.id").fetchall()
  if limit:
    pending = pending[:limit]
  counts = {"accept": 0, "reject": 0, "unknown": 0}
  if not pending:
    if log:
      log.info("no pending audit tasks")
    return counts

  import asyncio

  payloads = []
  with DictAdapter() as adapter:
    for task in pending:
      glosses = adapter.gloss_set(task["headword"])
      context = _context_of(task["parsed_json"])
      prompt = build_audit_prompt(
          task["headword"], glosses, task["raw_output"], context)
      payloads.append({
          "task_id": task["task_id"],
          "probe_id": task["probe_id"],
          "headword": task["headword"],
          "output": task["raw_output"],
          "prompt": prompt,
          "auditor_model": auditor_model,
          "cache_key": _cache_key(
              task["probe_id"], task["headword"], task["raw_output"],
              auditor_model),
      })

  store = _make_store(conn, counts, len(payloads), log=log)
  asyncio.run(_ask_all(runner, payloads, on_result=store))
  if log:
    log.info("audited %d task(s): %s", len(payloads), counts)
  return counts


def _make_store(conn, counts, total, log=None):
  """Builds the per-reply callback that records one audit verdict.

  Results are stored as they arrive rather than after every call has returned:
  an audit batch is hundreds of model calls and can take tens of minutes, and a
  crash must not discard completed work.  Each write is its own transaction, so
  a re-run only redoes the tasks still marked pending.
  """
  state = {"done": 0}

  def store(payload, reply):
    verdict = parse_verdict(reply)
    counts[verdict] += 1
    result = json.dumps({"verdict": verdict, "reply": (reply or "")[:200]},
                        ensure_ascii=False)
    with db_module.transaction(conn):
      conn.execute(
          "UPDATE audit_tasks SET status = 'done', result = ? WHERE id = ?",
          (result, payload["task_id"]))
    state["done"] += 1
    if log and state["done"] % 20 == 0:
      log.info("  audited %d/%d", state["done"], total)

  return store


async def _ask_all(runner, payloads, on_result=None):
  """Sends the audit prompts concurrently under the runner's semaphore.

  When ``on_result`` is given it is called with ``(payload, reply)`` as each
  reply arrives, so the caller can persist incrementally.
  """
  import asyncio
  import httpx

  semaphore = asyncio.Semaphore(runner._concurrency)  # noqa: SLF001

  async def one(client, payload):
    async with semaphore:
      body = {
          "model": runner._model,  # noqa: SLF001
          "messages": [{"role": "user", "content": payload["prompt"]}],
          "temperature": 0,
          "max_tokens": 8,
          "seed": 42,
      }
      if runner._capabilities.get("supports_disable_thinking"):  # noqa: SLF001
        body["chat_template_kwargs"] = runner._capabilities.get(  # noqa: SLF001
            "disable_thinking_kwargs", {"enable_thinking": False})
      try:
        response = await client.post(
            runner._base_url + "/chat/completions", json=body)  # noqa: SLF001
        if response.status_code != 200:
          return payload, None
        choice = (response.json().get("choices") or [{}])[0]
        return payload, (choice.get("message") or {}).get("content")
      except Exception:  # noqa: BLE001 - a failed audit is recorded as unknown
        return payload, None

  replies = []
  async with httpx.AsyncClient(timeout=runner._timeout) as client:  # noqa: SLF001
    tasks = [asyncio.create_task(one(client, payload)) for payload in payloads]
    for future in asyncio.as_completed(tasks):
      payload, reply = await future
      if on_result is not None:
        on_result(payload, reply)
      else:
        replies.append(reply)
  return replies


def summary(conn):
  """Computes auditor agreement against the rule-based judge.

  :returns: ``{"audited", "agreement", "recovery", "accept_rate_by_label"}``.
  """
  rows = conn.execute(
      "SELECT j.label AS label, at.result AS result FROM audit_tasks at "
      "JOIN probes p ON p.id = at.probe_id "
      "JOIN judgments j ON j.probe_id = p.id "
      "WHERE at.status = 'done' AND at.result IS NOT NULL").fetchall()
  by_label = {}
  accepted_from_kp = 0
  audited_kp = 0
  accepted_from_xu = 0
  audited_xu = 0
  for row in rows:
    try:
      verdict = json.loads(row["result"]).get("verdict")
    except (TypeError, ValueError):
      continue
    label = row["label"]
    by_label.setdefault(label, {"accept": 0, "reject": 0, "unknown": 0})
    by_label[label][verdict] = by_label[label].get(verdict, 0) + 1
    if label in ("K", "P"):
      audited_kp += 1
      accepted_from_kp += 1 if verdict == ACCEPT else 0
    else:
      audited_xu += 1
      accepted_from_xu += 1 if verdict == ACCEPT else 0
  return {
      "audited": len(rows),
      "agreement": round(accepted_from_kp / audited_kp, 4) if audited_kp else None,
      "recovery": round(accepted_from_xu / audited_xu, 4) if audited_xu else None,
      "audited_kp": audited_kp,
      "audited_xu": audited_xu,
      "accept_rate_by_label": by_label,
  }


def _context_of(parsed_json):
  if not parsed_json:
    return None
  try:
    return json.loads(parsed_json).get("sentence")
  except (TypeError, ValueError):
    return None
