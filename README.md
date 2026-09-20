# tkrzw-dict-agent

A read-only **English–Japanese vocabulary sidecar for AI agents**, built on the
[tkrzw-dict](https://github.com/estraier/tkrzw-dict) union dictionary.

It gives an agent **ranked candidate meanings** — with a domain tag and a score
— for the difficult words of an English text, together with pronunciation,
related words, and key-term extraction. It provides evidence for the caller to
choose from; it is not a translation service.

Implemented in **pure Python**. The Tkrzw `HashDBM` files are read directly, so
neither the Tkrzw C++ library nor its Python extension is required.

```console
$ dict lookup tightening --context "The Fed tightened monetary policy"
tightening  ˈtaɪtənɪŋ
- 締めつけ（経済） 0.5071
- 締付（経済） 0.4767
- 引き締め（経済） 0.4481
```

---

## Relation to upstream (estraier/tkrzw-dict)

This project is a **derivative work (fork) of
[`estraier/tkrzw-dict`](https://github.com/estraier/tkrzw-dict)**, published as
a separate repository.

What that means in practice:

- It is **not a GitHub fork**. The repository was created independently, so
  there is no fork-network link back to the upstream repository on GitHub. The
  upstream project remains the canonical source of the dictionary engine and
  data.
- **Upstream source is not vendored here.** [`scripts/setup.sh`](scripts/setup.sh)
  clones `estraier/tkrzw-dict` at the pinned commit
  `be2be5252e8a1ec28cc566ae64cd98cf85ff09fb` and applies
  [`patches/tkrzw-dict-use-pytkrzw.patch`](patches/tkrzw-dict-use-pytkrzw.patch),
  a two-line change that routes the upstream scripts' `import tkrzw` to this
  project's pure-Python reader instead of the C++ extension.
- **The union dictionary data is not tracked here.** It is a separate ~313 MB
  download from <https://dbmx.net/dict/union-dict-data.tar.gz>, fetched by the
  same setup script. Its license comes from the individual data sources, not
  from this repository.
- [`docs/spec/`](docs/spec/) holds documents that describe the upstream project
  and the union dictionary. They are included for reference and remain subject
  to the upstream license.

Upstream is licensed under the Apache License 2.0, Copyright 2020 Google LLC,
authored by Mikio Hirabayashi. See [NOTICE](NOTICE) for the full attribution.

To make the relationship explicit on GitHub as well, fork
`estraier/tkrzw-dict` and re-apply this repository's files on top, or ask the
upstream maintainer to link the two repositories.

## What it does

| Operation | Description |
|---|---|
| `lookup` | One word: pronunciation, translations, senses ranked by context |
| `resolve` | One word in context: meanings reordered by the surrounding text |
| `enrich` | A text: Japanese candidates for each difficult word |
| `extract_terms` | A text: the important terms, most important first |

Sense ranking combines three signals — corpus frequency, domain match, and
context similarity — and the context is expanded with each word's related terms,
which is what lets `"the river bank"` rank the river sense of *bank* above the
financial one.

The dictionary holds **499,750 headword records** (`union-body.tkh`, 582 MB),
with a reverse index, an inflection index, and an example-sentence corpus.

## Requirements

- Python 3.9 or newer (3.12 tested)
- `regex`
- Optional: `fastapi`, `uvicorn`, `httpx` for the HTTP API
- Disk: about 1 GB for the dictionary data
- **No C++ toolchain and no Tkrzw installation are needed**

## Install

```bash
git clone https://github.com/Fuchi-JPN/tkrzw-dict-agent.git
cd tkrzw-dict-agent
scripts/setup.sh                 # clone upstream, download data, create .venv
scripts/setup.sh --no-data       # skip the ~313 MB dictionary download
```

`setup.sh` is idempotent: re-running it leaves an existing checkout, data and
virtual environment in place.

Manual equivalent:

```bash
git clone https://github.com/estraier/tkrzw-dict.git
git -C tkrzw-dict checkout be2be5252e8a1ec28cc566ae64cd98cf85ff09fb
git -C tkrzw-dict apply patches/tkrzw-dict-use-pytkrzw.patch
curl -L -o tkrzw-dict/union-dict-data.tar.gz https://dbmx.net/dict/union-dict-data.tar.gz
tar xzf tkrzw-dict/union-dict-data.tar.gz -C tkrzw-dict/
python3 -m venv .venv
.venv/bin/pip install -e '.[api,dev]'
```

## Usage

### Command line

```bash
dict lookup tightening
dict lookup bank --context "The river bank was eroded by the flood."
dict enrich "The Fed's tightening monetary policy triggered a credit crunch."
dict extract "The semiconductor shortage disrupted global supply chains."

dict lookup tightening --json            # machine-readable
dict enrich - < article.txt              # read the text from stdin
```

Output is UTF-8 when redirected, so an agent harness can capture the raw bytes
and decode them explicitly. Interactive consoles are left on the platform
encoding.

### HTTP API

```bash
.venv/bin/dict-server                    # binds 0.0.0.0:8765
.venv/bin/dict-server --host 127.0.0.1   # loopback only
.venv/bin/dict-server --port 9000 --workers 4
```

| Method | Path | Body |
|---|---|---|
| `GET` | `/health` | – |
| `POST` | `/lookup` | `{"word": "...", "context": "..."}` |
| `POST` | `/enrich` | `{"text": "..."}` |
| `POST` | `/extract` | `{"text": "..."}` |

```bash
curl -s -X POST http://127.0.0.1:8765/enrich \
  -H 'Content-Type: application/json' \
  -d '{"text":"The doctor prescribed a drug for the chronic infection."}' \
  | python3 -m json.tool --no-ensure-ascii
```

Responses are `application/json; charset=utf-8`. Requests above 20,000
characters are rejected with 413.

> **Security:** the API has no authentication, CORS policy, rate limiting or
> TLS. Binding to `0.0.0.0` exposes it to every host that can reach the port.
> Put it behind a reverse proxy, or bind to `127.0.0.1` and use a tunnel.

### As an agent tool

[`skills/tkrzw-dict-vocabulary-support/`](skills/tkrzw-dict-vocabulary-support/)
contains an agent skill definition (`tkrzw-dict_SKILL.md`) that teaches another
host's agent how to drive the HTTP API, including the `vocabulary_support`
function definition, worked examples, error handling and Windows encoding
notes.

The same tool is available in-process:

```python
from tkrzw_dict_agent.agent_api import VocabularySupportHandler

with VocabularySupportHandler() as handler:
    result = handler.handle({"text": "The Fed's tightening policy", "word": "tightening"})
    print(result["hint"])
```

### Python library

```python
from tkrzw_dict_agent import VocabularySidecar

with VocabularySidecar() as sidecar:
    sidecar.lookup("tightening")
    sidecar.resolve("bank", "The river bank was eroded by the flood.")
    sidecar.enrich("The Fed's tightening monetary policy surprised markets.")
    sidecar.extract_terms("The semiconductor shortage disrupted supply chains.")
```

## Architecture

```
agent_api / cli          transport: tool handler, FastAPI, command line
        │
pipelines                batch enrich, term extraction, auto-fire conditions
        │
core/query.py            VocabularyQuery: lookup / resolve / enrich / extract_terms
   ├── core/scorer.py       frequency + domain + context scoring
   ├── core/normalizer.py   normalization, language detection, word extraction
   └── core/db.py           Dictionary access
             │
      core/upstream.py     isolates the vendored tkrzw-dict import
             │
      core/pytkrzw.py      pure-Python HashDBM reader (mmap, no C++)
             │
      union-*.tkh          memory-mapped dictionary data
```

`core/pytkrzw.py` reimplements the subset of the `tkrzw` API the dictionary
engine uses — read-only `HashDBM` access, plain-text line search, and the hash
and edit-distance utilities — from the Tkrzw on-disk format. Its output has been
verified byte-for-byte against the C++ binding.

## Tests

```bash
.venv/bin/python3 -m pytest tests/ -q      # 90 tests
```

Tests that need the dictionary data are skipped when it is absent. Set
`TKRZW_DICT_DIR` to point at a different dictionary checkout.

## Documentation

- [`docs/spec/`](docs/spec/) — specification documents (Japanese), including
  the detailed implementation specification
- [`NOTICE`](NOTICE) — upstream attribution and data licensing
- [`patches/`](patches/) — the patch applied to the upstream checkout

## Limitations

- **Candidate hints, not verified translations.** Sense ranking uses lexical
  overlap between the context and the sense definitions; without shared
  vocabulary it falls back to corpus frequency.
- **Domain tags are entry-level** and drawn from six labels only
  (経済 / 医療 / IT / 法律 / 科学 / 一般).
- **Read-only.** Building or updating dictionaries is out of scope.
- **Compressed `HashDBM` files are not supported**, only uncompressed,
  CRC-free ones — which is what the shipped data uses.
- **English source language only.**
- The `edit` (fuzzy) search mode scans every key and is slow (~10 s over the
  key list). The `lookup`/`resolve`/`enrich` paths do not use it.

## License

Apache License 2.0. See [LICENSE](LICENSE).

This project is a derivative work based on
[`tkrzw-dict`](https://github.com/estraier/tkrzw-dict) and the
[Tkrzw](https://dbmx.net/tkrzw/) key-value store, both Copyright 2020 Google
LLC, authored by Mikio Hirabayashi, and both licensed under the Apache License
2.0. See [NOTICE](NOTICE) for the full attribution and for the licensing of the
dictionary data, which is downloaded separately and governed by the terms of its
individual sources.
