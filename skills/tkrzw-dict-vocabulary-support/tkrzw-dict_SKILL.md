---
name: tkrzw-dict-vocabulary-support
description: "Query a remote Tkrzw-Dict vocabulary sidecar over HTTP for Japanese meanings of difficult English words. Use when: translating or reading English text that contains rare, technical, or domain-specific vocabulary; an English word's meaning is ambiguous and the context should decide the sense; you need a pronunciation, Japanese translation candidates, related words, or a domain tag for an English term; you need to extract the important/key terms of an English passage; or a small or non-English model is producing unstable translations of English terms. Also use when the user mentions '語彙サイドカー', 'vocabulary sidecar', 'Tkrzw-Dict', 'union dictionary', or asks for 和訳・訳語候補・重要語抽出 of English text. Returns ranked candidate senses for the caller to choose from, not a finished translation."
---

# Tkrzw-Dict Vocabulary Support (remote HTTP sidecar)

A read-only English–Japanese **dictionary sidecar** exposed as an HTTP API. It
returns **ranked candidate meanings** (with a domain tag and a score) for the
difficult words of an English text, plus pronunciation, related words, and
key-term extraction.

It is **not a translation service**. It provides evidence; the calling agent
chooses the sense that fits its context and produces the translation.

## Endpoint

The sidecar runs on a host reachable over the network. Ask the operator for the
host, or use the value in `$TKRZW_DICT_API` if it is set. The default host is 100.77.176.99.

```
BASE_URL = http://<sidecar-host>:8765      # default port is 8765
```

Always resolve the base URL once and reuse it:

```bash
BASE_URL="${TKRZW_DICT_API:-http://<sidecar-host>:8765}"
```

Verify reachability before the first call:

```bash
curl -s "$BASE_URL/health"
# {"status":"ok","tool":"vocabulary_support"}
```

If the health check fails, the sidecar is down, firewalled, or on a different
port. Do not retry in a tight loop; report the failure and continue without it.

## Operations

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `POST` | `/lookup` | One word: pronunciation, translations, ranked senses |
| `POST` | `/enrich` | A text: Japanese candidates for each difficult word |
| `POST` | `/extract` | A text: the important terms, most important first |

All `POST` bodies are JSON objects. Responses are JSON. Text is limited to
20,000 characters per request.

---

## `POST /lookup` — one word

Request:

```json
{"word": "bank", "context": "The river bank was eroded by the flood."}
```

- `word` (required) — the English word to look up. Inflected forms are
  resolved automatically (`ran` → `run`, `mice` → `mouse`).
- `context` (optional) — surrounding sentence or paragraph. **Supply it
  whenever you have one**: it reorders the senses so the contextually correct
  meaning ranks first, and it drives the domain tag.

Response:

```json
{
  "word": "bank",
  "found": true,
  "pronunciation": "bæŋk",
  "translations": ["銀行", "バンク", "土手", "..."],
  "related": ["repository", "deposit", "riverbank", "..."],
  "probability": 0.00074,
  "lemmas": [],
  "meanings": [
    {"ja": "バンク", "domain": "経済", "score": 0.4878, "word": "bank"},
    {"ja": "土手",   "domain": "経済", "score": 0.4766, "word": "bank"}
  ]
}
```

- `found: false` means the word is not in the dictionary (2.5M+ entries, so
  this is rare). Check `found` before using the lists.
- `meanings` is ordered **best first**. Use it, not the raw `translations`
  list, which is only corpus-frequency ordered.
- `score` is in `[0, 1]`; higher is a better fit for the given context.
- `lemmas` lists the base forms when `word` was an inflected form.
- `probability` is the corpus frequency of the word; very small values mean a
  rare (and often worth-explaining) word.

**Context changes the order.** The same word in different contexts yields
different `meanings`:

```bash
# river sense ranks first
curl -s -X POST "$BASE_URL/lookup" -H 'Content-Type: application/json' \
  -d '{"word":"bank","context":"The river bank was eroded by the flood."}'

# financial sense ranks first
curl -s -X POST "$BASE_URL/lookup" -H 'Content-Type: application/json' \
  -d '{"word":"bank","context":"The central bank raised interest rates."}'
```

---

## `POST /enrich` — a text

Request:

```json
{"text": "The Fed's tightening monetary policy triggered a credit crunch."}
```

Response:

```json
{
  "terms": [
    {
      "word": "tightening",
      "candidates": ["締めつけ", "締付", "引き締め", "..."],
      "meanings": [
        {"ja": "締めつけ", "domain": "経済", "score": 0.5036, "word": "tightening"}
      ]
    }
  ]
}
```

- One entry per dictionary-covered content word, in order of appearance.
  Function words (`the`, `of`, `and`, …) are already removed.
- `candidates` is the ordered list of Japanese translations.
- `meanings` adds `domain` and `score` per candidate; prefer it when you need
  to justify a choice.
- Unknown words are skipped silently, so `terms` may be shorter than expected,
  or empty for a fully out-of-vocabulary text.

---

## `POST /extract` — key terms

Request:

```json
{"text": "The central bank announced a tightening of monetary policy. Inflation rose and interest rates climbed."}
```

Response:

```json
{"terms": ["inflation", "central", "bank", "announced", "tightening", "monetary", "policy"]}
```

Returns important terms by TF-IDF, with proper nouns promoted and function
words removed. Use it to decide **which** words of a long passage deserve a
`/lookup` or `/enrich` call.

---

## Tool definition (for function-calling agents)

Register this tool if your runtime supports function calling:

```json
{
  "name": "vocabulary_support",
  "description": "Provide Japanese hints for difficult English words. Call this when the meaning of English terms is unclear: it returns candidate Japanese meanings for the important words of the text, so the caller can pick the sense that fits the context. It is a sidecar for disambiguation, not a translation service.",
  "parameters": {
    "type": "object",
    "properties": {
      "text": {"type": "string", "description": "The English text whose vocabulary should be explained."},
      "word": {"type": "string", "description": "A single word to resolve against the text. When given, only that word is explained."},
      "max_terms": {"type": "integer", "minimum": 1, "maximum": 100}
    },
    "required": ["text"]
  }
}
```

Map it onto the HTTP API as follows:

| Tool argument | HTTP call |
|---|---|
| `word` present | `POST /lookup` with `{"word": <word>, "context": <text>}` |
| `word` absent | `POST /enrich` with `{"text": <text>}` |

## When to call

Call the sidecar when any of these hold:

- The text is **English-dominant** and contains rare, technical, or
  domain-specific words.
- A word's meaning is **ambiguous** and the context should decide the sense.
- You are about to translate and want candidate **訳語** rather than guessing.
- You need to know which terms of a long passage are the important ones.

Do not call it for plain, high-frequency English, or when the text is not
English.

## How to use the result

1. Treat the response as **candidate senses**, not as the answer.
2. Pick the candidate that fits the context. The first entry of `meanings`
   (highest `score`) is the best guess, but verify it reads correctly.
3. The `（分野）` / `domain` tag (`経済`=economics, `医療`=medicine, `IT`,
   `法律`=law, `科学`=science, `一般`=general) tells you which domain the sense
   belongs to. A domain mismatch with the context is a signal to pick another
   candidate.
4. Produce your own translation using the chosen candidate. Never emit the
   whole candidate list to the user as if it were a translation.
5. `candidates`/`meanings` are still **unfiltered** for correctness: a word
   like `Fed` bundles unrelated senses (Federal Reserve / FBI agent / animal
   feed), so the ranking is a hint, not a guarantee.

## Client examples

### curl

```bash
BASE_URL="${TKRZW_DICT_API:-http://<sidecar-host>:8765}"

# one word in context
curl -s -X POST "$BASE_URL/lookup" \
  -H 'Content-Type: application/json' \
  -d '{"word":"tightening","context":"The Fed tightened monetary policy."}' \
  | python3 -m json.tool --no-ensure-ascii

# a sentence
curl -s -X POST "$BASE_URL/enrich" \
  -H 'Content-Type: application/json' \
  -d '{"text":"The doctor prescribed a drug for the chronic infection."}' \
  | python3 -m json.tool --no-ensure-ascii

# key terms
curl -s -X POST "$BASE_URL/extract" \
  -H 'Content-Type: application/json' \
  -d '{"text":"The semiconductor shortage disrupted global supply chains."}'
```

`--no-ensure-ascii` keeps the Japanese readable instead of `\uXXXX` escapes.

### Python (standard library, no dependencies)

```python
import json
import urllib.error
import urllib.request

BASE_URL = "http://<sidecar-host>:8765"


def _post(path, payload, timeout=30):
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            "sidecar {} failed: HTTP {} {}".format(
                path, error.code, error.read().decode("utf-8", "replace")))


def lookup(word, context=""):
    """Ranked Japanese senses for one word."""
    return _post("/lookup", {"word": word, "context": context})


def enrich(text):
    """Japanese candidates for each difficult word of the text."""
    return _post("/enrich", {"text": text})


def extract_terms(text):
    """Important terms, most important first."""
    return _post("/extract", {"text": text})["terms"]
```

### Python (requests)

Requires `pip install requests`.

```python
import requests

BASE_URL = "http://<sidecar-host>:8765"

response = requests.post(
    f"{BASE_URL}/enrich",
    json={"text": "The Fed's tightening policy triggered a credit crunch."},
    timeout=30)
response.raise_for_status()
for term in response.json()["terms"]:
    best = term["meanings"][0]
    print(f'{term["word"]}: {best["ja"]} ({best["domain"]}, score={best["score"]})')
```

### Shell one-liner for an agent working over SSH

```bash
ssh <sidecar-host> 'curl -s -X POST http://127.0.0.1:8765/lookup \
  -H "Content-Type: application/json" \
  -d "{\"word\":\"tightening\",\"context\":\"Fed policy\"}"'
```

## Errors and limits

| Status | Meaning | Action |
|---|---|---|
| 200 | Success | Parse the JSON body |
| 400 | Missing or invalid field (`word`/`text`), or body is not a JSON object | Fix the request; check the required field is a non-empty string |
| 404 | Unknown path | Check the endpoint spelling |
| 413 | Text longer than 20,000 characters | Split the text and call again per chunk |
| 5xx | Server-side failure | Report it; do not retry in a tight loop |
| connection error | Sidecar down, wrong host/port, or firewalled | Confirm `GET /health`; ask the operator |

Other practical notes:

- Timeouts: allow at least 30 seconds. Typical latency is a few milliseconds
  per word, but the first call after a restart also loads the dictionary.
- Send `Content-Type: application/json`; several servers reject a missing
  header before parsing.
- The API is **read-only** — it never stores or mutates dictionary data.

## Windows and console encoding

Japanese output is UTF-8. On Windows this is the usual source of mojibake.

**Prefer the HTTP API.** It sends `Content-Type: application/json; charset=utf-8`
and no console is involved, so reading the response body and decoding it as
UTF-8 always works:

```python
import json, urllib.request
with urllib.request.urlopen(request, timeout=30) as response:
    payload = json.loads(response.read().decode("utf-8"))   # explicit, reliable
```

**If you must drive the CLI through PowerShell**, be aware of the pipeline: a
native program's UTF-8 bytes are decoded by PowerShell using
`[Console]::OutputEncoding`, which on Windows PowerShell 5.1 defaults to the
ANSI code page. The result is mojibake such as `ç· ãã¤ãã` for `締めつけ`.

The CLI emits UTF-8 whenever its output is redirected (piped or captured),
which is the case for a harness. So the fix is on the reading side:

```powershell
# PowerShell 7
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# Windows PowerShell 5.1
chcp 65001 > $null
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
```

Alternatively capture the raw bytes and decode explicitly, or bypass the
console entirely with the HTTP API:

```powershell
$response = Invoke-WebRequest -Uri "$BASE_URL/lookup" -Method Post `
  -ContentType 'application/json' `
  -Body '{"word":"tightening","context":"Fed policy"}'
[System.Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
```

Notes:

- Redirected CLI output is UTF-8, so byte-level capture plus explicit UTF-8
  decoding is a stable contract in both directions (stdin is also read as
  UTF-8 when redirected, for `dict enrich -`).
- Interactive consoles are unchanged: they render Unicode natively, so output
  may look correct in a terminal while a captured pipe looks wrong.
- `--json` changes the shape, not the encoding.
- An explicit `PYTHONIOENCODING` overrides the CLI default and disables the
  UTF-8 pinning; set it only if you know the consumer's encoding.

## Limitations to keep in mind

- **Candidate hints, not verified translations.** Sense ranking uses lexical
  overlap between the context and the sense definitions; if the context shares
  no vocabulary with the gloss, the order falls back to corpus frequency.
- **Domain tags are entry-level.** A word bundling several domains gets one
  representative tag; the tag may not match every candidate.
- **Only 6 domain labels** (`経済`/`医療`/`IT`/`法律`/`科学`/`一般`).
- **No authentication or CORS** is built into the service. Do not call it from
  untrusted browser contexts, and do not expose the port beyond the intended
  network.
- English is the supported source language. Japanese input returns nothing
  useful.
