# tkrzw-dict-agent

**Languages:** [English](#english) · [日本語](#日本語)

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

## English

### Relation to upstream (estraier/tkrzw-dict)

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

### What it does

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

### Requirements

- Python 3.9 or newer (3.12 tested)
- `regex`
- Optional: `fastapi`, `uvicorn`, `httpx` for the HTTP API
- Disk: about 1 GB for the dictionary data
- **No C++ toolchain and no Tkrzw installation are needed**

### Install

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

### Usage

#### Command line

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

#### HTTP API

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

#### As an agent tool

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

#### Python library

```python
from tkrzw_dict_agent import VocabularySidecar

with VocabularySidecar() as sidecar:
    sidecar.lookup("tightening")
    sidecar.resolve("bank", "The river bank was eroded by the flood.")
    sidecar.enrich("The Fed's tightening monetary policy surprised markets.")
    sidecar.extract_terms("The semiconductor shortage disrupted supply chains.")
```

### Architecture

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

### Tests

```bash
.venv/bin/python3 -m pytest tests/ -q      # 90 tests
```

Tests that need the dictionary data are skipped when it is absent. Set
`TKRZW_DICT_DIR` to point at a different dictionary checkout.

### Documentation

- [`docs/spec/`](docs/spec/) — specification documents (Japanese), including
  the detailed implementation specification
- [`NOTICE`](NOTICE) — upstream attribution and data licensing
- [`patches/`](patches/) — the patch applied to the upstream checkout

### Limitations

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

### License

Apache License 2.0. See [LICENSE](LICENSE).

This project is a derivative work based on
[`tkrzw-dict`](https://github.com/estraier/tkrzw-dict) and the
[Tkrzw](https://dbmx.net/tkrzw/) key-value store, both Copyright 2020 Google
LLC, authored by Mikio Hirabayashi, and both licensed under the Apache License
2.0. See [NOTICE](NOTICE) for the full attribution and for the licensing of the
dictionary data, which is downloaded separately and governed by the terms of its
individual sources.

---

## 日本語

[tkrzw-dict](https://github.com/estraier/tkrzw-dict) 統合辞書を基盤とした、
**AI エージェント向けの読み取り専用 英和語彙サイドカー**です。

英文中の難しい語について、**順位付けした語義候補**（分野タグとスコア付き）を、
発音・関連語・重要語抽出とあわせて提示します。呼び出し側が選ぶための根拠を
提供するものであり、翻訳サービスではありません。

**Pure Python** で実装しています。Tkrzw の `HashDBM` ファイルを直接読むため、
Tkrzw C++ ライブラリもその Python 拡張も不要です。

```console
$ dict lookup tightening --context "The Fed tightened monetary policy"
tightening  ˈtaɪtənɪŋ
- 締めつけ（経済） 0.5071
- 締付（経済） 0.4767
- 引き締め（経済） 0.4481
```

### 上流リポジトリとの関係（estraier/tkrzw-dict）

本プロジェクトは
[`estraier/tkrzw-dict`](https://github.com/estraier/tkrzw-dict) の
**派生物（Fork）** であり、独立したリポジトリとして公開しています。

実際の関係は次のとおりです。

- **GitHub の Fork ではありません。** 本リポジトリは独立に作成したため、
  GitHub 上で上流リポジトリへ戻る Fork ネットワークのリンクはありません。
  辞書エンジンとデータの正典は、引き続き上流プロジェクトです。
- **上流ソースは同梱していません。** [`scripts/setup.sh`](scripts/setup.sh)
  が `estraier/tkrzw-dict` をピン留めしたコミット
  `be2be5252e8a1ec28cc566ae64cd98cf85ff09fb` で clone し、
  [`patches/tkrzw-dict-use-pytkrzw.patch`](patches/tkrzw-dict-use-pytkrzw.patch)
  を適用します。これは上流スクリプトの `import tkrzw` を、C++ 拡張ではなく
  本プロジェクトの Pure Python リーダーへ向ける 2 行の変更です。
- **統合辞書データは同梱していません。** 別途約 313 MB を
  <https://dbmx.net/dict/union-dict-data.tar.gz> からダウンロードします
  （上記の setup スクリプトが取得します）。そのライセンスは個々のデータ
  ソースに由来し、本リポジトリのライセンスではありません。
- [`docs/spec/`](docs/spec/) には、上流プロジェクトと統合辞書を説明する
  文書を収めています。参照用であり、上流のライセンスに従います。

上流は Apache License 2.0、Copyright 2020 Google LLC、著者は平林幹雄氏です。
完全な帰属表示は [NOTICE](NOTICE) を参照してください。

GitHub 上でも関係を明示したい場合は、`estraier/tkrzw-dict` を Fork して
本リポジトリのファイルを重ねるか、上流の管理者に両リポジトリのリンクを
依頼してください。

### 機能

| 操作 | 説明 |
|---|---|
| `lookup` | 単語 1 語：発音、訳語、文脈で順位付けした語義 |
| `resolve` | 文脈中の単語 1 語：周囲のテキストで語義を再順位付け |
| `enrich` | テキスト：難しい語ごとの日本語候補 |
| `extract_terms` | テキスト：重要な語を重要度順に抽出 |

語義の順位付けは 3 つの信号 — コーパス頻度、分野一致、文脈類似度 — を
組み合わせます。さらに文脈を各語の関連語で展開するため、`"the river
bank"` では *bank* の川岸の語義が金融の語義より上位に来ます。

辞書は **499,750 件の見出し語レコード**（`union-body.tkh`、582 MB）を収め、
和英索引、屈折索引、例文コーパスを備えます。

### 動作要件

- Python 3.9 以上（3.12 で検証）
- `regex`
- 任意：HTTP API を使う場合は `fastapi`、`uvicorn`、`httpx`
- ディスク：辞書データ用に約 1 GB
- **C++ ツールチェーンも Tkrzw のインストールも不要です**

### インストール

```bash
git clone https://github.com/Fuchi-JPN/tkrzw-dict-agent.git
cd tkrzw-dict-agent
scripts/setup.sh                 # 上流を clone、データを取得、.venv を作成
scripts/setup.sh --no-data       # 約 313 MB の辞書ダウンロードを省略
```

`setup.sh` は冪等です。再実行しても既存のチェックアウト、データ、仮想環境は
そのまま残ります。

手動で行う場合:

```bash
git clone https://github.com/estraier/tkrzw-dict.git
git -C tkrzw-dict checkout be2be5252e8a1ec28cc566ae64cd98cf85ff09fb
git -C tkrzw-dict apply patches/tkrzw-dict-use-pytkrzw.patch
curl -L -o tkrzw-dict/union-dict-data.tar.gz https://dbmx.net/dict/union-dict-data.tar.gz
tar xzf tkrzw-dict/union-dict-data.tar.gz -C tkrzw-dict/
python3 -m venv .venv
.venv/bin/pip install -e '.[api,dev]'
```

### 使い方

#### コマンドライン

```bash
dict lookup tightening
dict lookup bank --context "The river bank was eroded by the flood."
dict enrich "The Fed's tightening monetary policy triggered a credit crunch."
dict extract "The semiconductor shortage disrupted global supply chains."

dict lookup tightening --json            # 機械可読
dict enrich - < article.txt              # 標準入力から読む
```

出力はリダイレクト時に UTF-8 になります。エージェントハーネスは生バイトを
取得して明示的に UTF-8 で復号できます。対話的なコンソールはプラットフォームの
エンコーディングのままです。

#### HTTP API

```bash
.venv/bin/dict-server                    # 0.0.0.0:8765 で待受
.venv/bin/dict-server --host 127.0.0.1   # ループバック限定
.venv/bin/dict-server --port 9000 --workers 4
```

| メソッド | パス | 本文 |
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

応答は `application/json; charset=utf-8` です。20,000 文字を超えるリクエストは
413 で拒否されます。

> **セキュリティ:** 本 API は認証、CORS ポリシー、レート制限、TLS を持ちません。
> `0.0.0.0` にバインドすると、そのポートに到達できる全ホストへ公開されます。
> リバースプロキシの背後に置くか、`127.0.0.1` にバインドしてトンネルを使って
> ください。

#### エージェントツールとして

[`skills/tkrzw-dict-vocabulary-support/`](skills/tkrzw-dict-vocabulary-support/)
に、他ホストのエージェントへ HTTP API の操作方法を伝えるスキル定義
（`tkrzw-dict_SKILL.md`）があります。`vocabulary_support` の関数定義、実行例、
エラー処理、Windows のエンコーディングに関する注意を含みます。

同じツールはプロセス内からも利用できます。

```python
from tkrzw_dict_agent.agent_api import VocabularySupportHandler

with VocabularySupportHandler() as handler:
    result = handler.handle({"text": "The Fed's tightening policy", "word": "tightening"})
    print(result["hint"])
```

#### Python ライブラリ

```python
from tkrzw_dict_agent import VocabularySidecar

with VocabularySidecar() as sidecar:
    sidecar.lookup("tightening")
    sidecar.resolve("bank", "The river bank was eroded by the flood.")
    sidecar.enrich("The Fed's tightening monetary policy surprised markets.")
    sidecar.extract_terms("The semiconductor shortage disrupted supply chains.")
```

### アーキテクチャ

```
agent_api / cli          トランスポート：ツールハンドラ、FastAPI、コマンドライン
        │
pipelines                バッチ enrich、重要語抽出、自動発火条件
        │
core/query.py            VocabularyQuery: lookup / resolve / enrich / extract_terms
   ├── core/scorer.py       頻度＋分野＋文脈のスコアリング
   ├── core/normalizer.py   正規化、言語判定、語抽出
   └── core/db.py           辞書アクセス
             │
      core/upstream.py     同梱する tkrzw-dict の import を隔離
             │
      core/pytkrzw.py      Pure Python の HashDBM リーダー（mmap、C++ 不使用）
             │
      union-*.tkh          メモリマップした辞書データ
```

`core/pytkrzw.py` は、辞書エンジンが使う `tkrzw` API の部分集合 — 読み取り
専用の `HashDBM` アクセス、プレーンテキストの行検索、ハッシュと編集距離の
ユーティリティ — を Tkrzw のオンディスク形式から再実装したものです。その出力は
C++ バインディングとバイト単位で一致することを検証済みです。

### テスト

```bash
.venv/bin/python3 -m pytest tests/ -q      # 90 件
```

辞書データを必要とするテストは、データが無い場合スキップされます。別の辞書
チェックアウトを指す場合は `TKRZW_DICT_DIR` を設定してください。

### ドキュメント

- [`docs/spec/`](docs/spec/) — 仕様書（日本語）。詳細実装仕様書を含みます
- [`NOTICE`](NOTICE) — 上流への帰属表示とデータライセンス
- [`patches/`](patches/) — 上流チェックアウトへ適用するパッチ

### 制限事項

- **候補の提示であり、検証済みの翻訳ではありません。** 語義の順位付けは
  文脈と語義定義文の語彙的な重なりを使うため、共通語が無い場合は
  コーパス頻度にフォールバックします。
- **分野タグはエントリ単位**で、6 種類のみです
  （経済 / 医療 / IT / 法律 / 科学 / 一般）。
- **読み取り専用です。** 辞書の構築・更新は対象外です。
- **圧縮された `HashDBM` には対応しません。** 非圧縮かつ CRC 無しのもののみで、
  配布データはその形式です。
- **英語の原語のみ**を対象とします。
- `edit`（曖昧一致）検索モードは全キーを走査するため低速です（キー一覧で
  約 10 秒）。`lookup`／`resolve`／`enrich` はこの経路を使いません。

### ライセンス

Apache License 2.0。[LICENSE](LICENSE) を参照してください。

本プロジェクトは [`tkrzw-dict`](https://github.com/estraier/tkrzw-dict) と
[Tkrzw](https://dbmx.net/tkrzw/) キーバリューストアに基づく派生物です。
いずれも Copyright 2020 Google LLC、著者は平林幹雄氏で、Apache License 2.0 の
もとで頒布されています。完全な帰属表示と、別途ダウンロードする辞書データの
ライセンス（各データソースの条件に従います）については [NOTICE](NOTICE) を
参照してください。

---

**[↑ English](#english)** · [日本語](#日本語)
