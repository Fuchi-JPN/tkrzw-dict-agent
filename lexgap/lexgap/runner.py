"""ProbeRunner: sends prompts to the model and caches every answer.

The design is dictated by two properties of the runner, both measured in M0:

* **Thinking must be disabled.**  The model is a reasoning model; by default it
  writes its reasoning into ``message.content`` (200 tokens for a one-word
  answer) and the judge would be comparing prose to a gloss.  Every request
  therefore carries ``chat_template_kwargs = {"enable_thinking": false}``,
  which brought the same answer down to 2 tokens.
* **The runner is a single device.**  Concurrency is capped by
  ``max_concurrency``; HTTP calls run concurrently while database writes stay
  strictly serial, so SQLite is never touched from two tasks at once.

Resumability comes from the ``probes`` UNIQUE constraint.  Existing rows are
looked up before any request is made, so re-running an interrupted batch costs
nothing for the probes already done.  A probe that fails all retries is stored
with ``raw_output = NULL`` and an error in ``parsed_json``, which makes it
distinguishable from an unanswered probe and re-runnable deliberately.
"""

import asyncio
import json
import time

from . import config, db as db_module, prompter
from .dict_adapter import DictAdapter

__all__ = ["ProbeRunner", "RunSummary"]

PROBE_COMPLETE = "complete"
PROBE_ERROR = "error"

# A translation answer is a few tokens; the cap only bounds a runaway response.
DEFAULT_MAX_TOKENS = 32


class RunSummary:
  """Counts from one probe run."""

  def __init__(self):
    self.requested = 0
    self.cached = 0
    self.sent = 0
    self.failed = 0
    self.prompt_tokens = 0
    self.completion_tokens = 0
    self.latencies = []

  def as_dict(self):
    latencies = sorted(self.latencies)
    def percentile(fraction):
      if not latencies:
        return 0.0
      index = min(len(latencies) - 1, int(len(latencies) * fraction))
      return latencies[index]
    return {
        "requested": self.requested,
        "cached": self.cached,
        "sent": self.sent,
        "failed": self.failed,
        "prompt_tokens": self.prompt_tokens,
        "completion_tokens": self.completion_tokens,
        "latency_p50_s": round(percentile(0.5), 3),
        "latency_p95_s": round(percentile(0.95), 3),
        "latency_mean_s": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
    }


class ProbeRunner:
  """Runs P1/W1 probes for a sample set against one model."""

  def __init__(self, conn, runner_name="primary", log=None, max_tokens=None):
    self._conn = conn
    self._log = log
    self._runners = config.load_runners()["runner"]
    if runner_name not in self._runners:
      raise ValueError("unknown runner: {}".format(runner_name))
    self._runner_name = runner_name
    self._runner = self._runners[runner_name]
    self._base_url = self._runner["base_url"].rstrip("/")
    self._model = (self._runner.get("default_model", {}).get("name")
                   or self._runner.get("model_id"))
    self._model_id = self._runner.get("model_id") or self._model
    self._concurrency = int(self._runner.get("max_concurrency", 1))
    self._timeout = float(self._runner.get("request_timeout_s", 300))
    self._max_retries = int(self._runner.get("max_retries", 3))
    self._backoff = float(self._runner.get("retry_backoff_s", 2.0))
    self._capabilities = self._runner.get("capabilities", {})
    self._max_tokens = int(max_tokens or DEFAULT_MAX_TOKENS)

  @property
  def model_id(self):
    return self._model_id

  def build_plan(self, set_id, tasks=("P1",), layers=("main",), limit=None,
                 seed=42, rerun_errors=False):
    """Builds the list of probes to send, skipping anything already cached.

    :returns: ``(items, summary)`` where items are dictionaries ready to send.
    """
    summary = RunSummary()
    samples = self._load_samples(set_id, layers, limit)
    summary.requested = len(samples) * len(tasks)
    items = []
    cached = 0
    errors = 0
    with DictAdapter() as adapter:
      for sample in samples:
        matches = adapter.lookup(sample["headword"])
        if not matches:
          self._log.warning("headword not found in dictionary: %s", sample["headword"])
          continue
        record = matches[0]
        for task in tasks:
          probe = prompter.build_probe(record, task=task)
          existing = self._conn.execute(
              "SELECT id, raw_output, parsed_json FROM probes WHERE set_id=? AND "
              "headword=? AND model_id=? AND task=? AND prompt_ver=?",
              (set_id, sample["headword"], self._model_id, probe["task"],
               probe["prompt_ver"])).fetchone()
          if existing:
            if (rerun_errors and existing["raw_output"] is None
                and _is_error(existing["parsed_json"])):
              errors += 1
            else:
              cached += 1
              continue
          items.append({
              "set_id": set_id,
              "headword": sample["headword"],
              "layer": sample["layer"],
              "quantile": sample["quantile"],
              "task": probe["task"],
              "prompt_ver": probe["prompt_ver"],
              "prompt": probe["prompt"],
              "word": probe["word"],
              "sentence": probe["sentence"],
          })
    summary.cached = cached
    self._log.info(
        "plan: %d to send, %d cached, %d error(s) to retry (layers=%s, tasks=%s)",
        len(items), cached, errors, ",".join(layers), ",".join(tasks))
    return items, summary

  def run(self, set_id, tasks=("P1",), layers=("main",), limit=None, seed=42,
          rerun_errors=False):
    """Runs the probes and returns a :class:`RunSummary`."""
    items, summary = self.build_plan(
        set_id, tasks=tasks, layers=layers, limit=limit, seed=seed,
        rerun_errors=rerun_errors)
    if not items:
      self._log.info("nothing to do")
      return summary
    started = time.perf_counter()
    asyncio.run(self._run_async(items, summary, seed=seed))
    elapsed = time.perf_counter() - started
    self._log.info("done: %d sent, %d failed, %.1fs (%.2fs/probe, concurrency %d)",
                   summary.sent, summary.failed, elapsed,
                   elapsed / max(summary.sent, 1), self._concurrency)
    return summary

  # -- internals ---------------------------------------------------------
  def _load_samples(self, set_id, layers, limit):
    marks = ",".join("?" for _ in layers)
    sql = ("SELECT headword, layer, quantile FROM samples "
           "WHERE set_id = ? AND layer IN ({}) ORDER BY id".format(marks))
    rows = self._conn.execute(sql, (set_id, *layers)).fetchall()
    samples = [dict(row) for row in rows]
    if limit:
      samples = samples[:limit]
    return samples

  async def _run_async(self, items, summary, seed):
    import httpx

    semaphore = asyncio.Semaphore(self._concurrency)
    progress = _Progress(self._log, len(items))

    async with httpx.AsyncClient(timeout=self._timeout) as client:
      async def worker(item):
        async with semaphore:
          return await self._probe(client, item, seed)

      tasks = [asyncio.create_task(worker(item)) for item in items]
      for future in asyncio.as_completed(tasks):
        result = await future
        self._store(result)
        summary.sent += 1
        summary.prompt_tokens += result.get("tokens_in") or 0
        summary.completion_tokens += result.get("tokens_out") or 0
        if result.get("latency_ms"):
          summary.latencies.append(result["latency_ms"] / 1000.0)
        if result.get("error"):
          summary.failed += 1
        progress.tick()

  async def _probe(self, client, item, seed):
    """Sends one probe with retries and returns a storable result."""
    payload = {
        "model": self._model,
        "messages": [{"role": "user", "content": item["prompt"]}],
        "temperature": 0,
        "max_tokens": self._max_tokens,
        "seed": seed,
    }
    if self._capabilities.get("supports_disable_thinking"):
      payload["chat_template_kwargs"] = self._capabilities.get(
          "disable_thinking_kwargs", {"enable_thinking": False})

    result = dict(item)
    attempt = 0
    while True:
      attempt += 1
      start = time.perf_counter()
      try:
        response = await client.post(self._base_url + "/chat/completions", json=payload)
        latency_ms = int((time.perf_counter() - start) * 1000)
        if response.status_code != 200:
          raise RuntimeError("HTTP {}: {}".format(
              response.status_code, response.text[:200]))
        body = response.json()
        choice = (body.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = body.get("usage") or {}
        result.update({
            "raw_output": content.strip(),
            "parsed_json": json.dumps(
                {"task": item["task"], "sentence": item["sentence"],
                 "finish_reason": choice.get("finish_reason")},
                ensure_ascii=False),
            "tokens_in": usage.get("prompt_tokens"),
            "tokens_out": usage.get("completion_tokens"),
            "latency_ms": latency_ms,
            "error": None,
        })
        return result
      except Exception as exc:  # noqa: BLE001 - retried below
        if attempt >= self._max_retries:
          result.update({
              "raw_output": None,
              "parsed_json": json.dumps(
                  {"error": "{}: {}".format(type(exc).__name__, exc),
                   "attempts": attempt}, ensure_ascii=False),
              "tokens_in": None,
              "tokens_out": None,
              "latency_ms": int((time.perf_counter() - start) * 1000),
              "error": str(exc),
          })
          self._log.warning("probe failed after %d attempts: %s (%s)",
                            attempt, item["headword"], exc)
          return result
        await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))

  def _store(self, result):
    """Writes one probe in its own transaction.

    A single probe per transaction is the plan's rule: a crash cannot leave a
    partially written probe, and the UNIQUE constraint keeps a re-run from
    duplicating work.
    """
    with db_module.transaction(self._conn):
      self._conn.execute(
          "INSERT INTO probes (set_id, headword, model_id, task, prompt_ver, "
          "prompt_text, raw_output, parsed_json, tokens_in, tokens_out, "
          "latency_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
          (result["set_id"], result["headword"], self._model_id, result["task"],
           result["prompt_ver"], result["prompt"], result["raw_output"],
           result["parsed_json"], result["tokens_in"], result["tokens_out"],
           result["latency_ms"], db_module.utcnow()))


class _Progress:
  """Logs at each 10% boundary, as the plan requires."""

  def __init__(self, log, total):
    self._log = log
    self._total = max(total, 1)
    self._done = 0
    self._next = 10

  def tick(self):
    self._done += 1
    percent = self._done * 100 // self._total
    if percent >= self._next:
      self._log.info("  progress: %d%% (%d/%d)", percent, self._done, self._total)
      self._next = (percent // 10 + 1) * 10


def _is_error(parsed_json):
  if not parsed_json:
    return False
  try:
    return "error" in json.loads(parsed_json)
  except (TypeError, ValueError):
    return False
