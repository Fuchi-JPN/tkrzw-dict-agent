# LexGap

A verification system for **Japanese vocabulary gaps in small local LLMs**.

LexGap measures which Japanese words a small model fails to render in context,
models that failure as a probability, exports a gate a sidecar can apply, and
evaluates whether gating degrades translation quality while saving tokens.

It is a subproject of the [`tkrzw-dict-agent`](../) repository and uses that
project's union dictionary as its source of ground truth. It is a research
harness, not a service.

> **Status: M0 complete.** The environment is verified and the two open
> questions that block the sampler have been resolved by measurement (see
> [M0 findings](#m0-findings)). M1 (sampling and the first probe run) is next.

## Pipeline

```
sampler -> probe -> judge -> features -> predictor -> exporter -> intervention
                                                              |
                                                          reporter
```

Each stage writes to SQLite and is resumable: re-running a command continues
from the cached rows rather than repeating model calls. The `probes` table's
UNIQUE constraint over `(set_id, headword, model_id, task, prompt_ver)` *is* the
cache.

## Layout

```
lexgap/
├── lexgap/               package
│   ├── config.py         TOML loading and path resolution
│   ├── db.py             SQLite schema, connections, transactions
│   ├── dict_adapter.py   read-only view of the union dictionary
│   ├── normalizer.py     Japanese normalisation for judging
│   └── diagnostics.py    M0 dictionary report and environment check
├── configs/
│   ├── runners.toml      LLM runner connection
│   ├── models.toml       model registry (family, quantization, sha256)
│   └── prompts/          versioned prompt templates
├── specs/
│   └── s1.toml           sampling spec (versioned; frozen into the DB)
├── data/                 lexgap.db, corpora (not tracked)
├── logs/                 run logs (not tracked)
├── scripts/
│   ├── m0_envcheck.py    environment check
│   └── m0_investigate.py full-scan field investigation
├── tests/
└── lexgap_cli.py         command line entry point
```

## Setup

```bash
cd lexgap
python3 -m venv .venv
.venv/bin/pip install -e ..            # tkrzw-dict-agent: dictionary reader
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python3 lexgap_cli.py init-db
.venv/bin/python3 scripts/m0_envcheck.py
```

The dictionary itself comes from the parent project; run `../scripts/setup.sh`
if `../tkrzw-dict/union-body.tkh` is missing.

## Usage

```bash
# Create or verify the schema (idempotent)
.venv/bin/python3 lexgap_cli.py init-db

# Dictionary coverage: the Q-01 / Q-02 answers, with scan throughput
.venv/bin/python3 lexgap_cli.py dict-stats
.venv/bin/python3 lexgap_cli.py dict-stats --json

# Pre-flight for a probe run
.venv/bin/python3 lexgap_cli.py envcheck
```

`envcheck` exits non-zero if any item fails, so it can gate a batch run.

Commands for the experiment stages (`sample`, `probe`, `judge`, `audit`, `fit`,
`export`, `intervene`, `report`) are added with their milestones and are not
listed until they do something.

## M0 findings

Both were resolved by scanning all 499,750 keys / 501,069 entries, not by
assumption.

### Q-01 — the frequency field is left-censored

`probability` is present on **100%** of entries and is the only viable
frequency field (`aoa_base` 1.1%, `aoa_concept` 13.3%, `share` 1.9% are far too
sparse). But it has a **reporting floor of `1e-07`**, and **38%** of entries
store `0` because they fall below it. Those entries are not scattered: they are
the bottom 30% by frequency rank.

| frequency rank | share with probability 0 |
|---|---|
| 0-40% | ~0% |
| 40-60% | ~0% |
| 60-70% | 79.5% |
| 70-100% | 100% |

The plain-text key list `union-keys.txt` is frequency-ordered and complete
(499,750 lines = 499,750 records), so it supplies what `probability` cannot.

**Resolution:** stratified sampling cuts its quantiles on `freq_rank`
(full coverage); `log_freq`, censored at `1e-07`, stays available as a
predictor feature. The plan's external `wordfreq` fallback is not needed.

### Q-02 — examples are entry-level, not sense-level

Of **1,186,371** example objects, **none** carries a marker tying it to a
specific sense. Examples belong to the entry as a whole. Condition C4
(sense-level gating) therefore cannot use examples; the plan's fallback
applies and the top-scoring sense is used instead.

Related coverage, which constrains the sampler:

| quantity | value |
|---|---|
| entries with a Japanese translation | 85.8% |
| entries with no translation, unrecoverable from senses | 14.2% |
| senses with a `[translation]:` list | 21.6% (223,665 of 1,036,050) |
| entries with examples | 65.5% |

**Resolution:** the sampler requires a translation, since a probe without a
Japanese gloss has no ground truth to judge against. The 14.2% without one are
excluded as targets.

### Throughput

| operation | measured |
|---|---|
| raw key read (all 499,750) | **12.1 s** (24 µs/key) |
| full scan with record decoding + domain derivation | **97 s** (194 µs/key) |
| top-20,000 keys (largest records) | 19.3 s (966 µs/key) |

Domain derivation dominates the full scan. It is a one-time cost per sampling
run, not a per-probe cost.

### Runner

A local llama.cpp server answers on `http://127.0.0.1:8080/v1` and supports
`logprobs`. At M0 it was serving `Qwen3-Embedding-8B`, an **embedding** model:
`envcheck` reports this as a failure, because probes need a generation model
(Q-03). The runner is otherwise ready.

## Testing

```bash
.venv/bin/python3 -m pytest tests/ -q      # 47 tests
```

Tests that need the dictionary are skipped when it is absent.

## Roadmap

| milestone | content |
|---|---|
| M0 | environment, schema, dictionary adapter, Q-01/Q-02 — **done** |
| M1 | S1 sampling, P1 probes on one model, judge, 200-item audit, frequency curve |
| M2 | P2, feature builder, predictor (B0/B1/full), θ selection |
| M3 | quantization and cross-model comparison (H3, H4) |
| M4 | intervention evaluation C0-C5, non-inferiority test (AC2) |
| M5 | exporter, `gate.json`, `enrich --gate` in tkrzw-dict-agent |

## License

Apache License 2.0, inherited from the parent repository. See
[`../LICENSE`](../LICENSE) and [`../NOTICE`](../NOTICE) for the upstream
attribution and the licensing of the dictionary data.
