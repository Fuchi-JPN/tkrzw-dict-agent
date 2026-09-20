# Tkrzw-Dict Python版 for AI Agents 詳細仕様書

**版:** 1.4
**作成日:** 2026-09-20
**対象:** 実装済みモジュール `tkrzw_dict_agent` の詳細仕様
**上位仕様:** `Tkrzw-Dict Python版 for AI Agents― AIエージェント向け語彙サイドカー拡張モジュール仕様書 ―`（以下「上位仕様書」）

---

## 目次

1. [目的と適用範囲](#1-目的と適用範囲)
2. [用語定義](#2-用語定義)
3. [システム概要](#3-システム概要)
4. [実行環境と依存関係](#4-実行環境と依存関係)
5. [ディレクトリ構成](#5-ディレクトリ構成)
6. [データ資産仕様](#6-データ資産仕様)
7. [core/pytkrzw — Pure Python Tkrzw 実装](#7-corepytkrzw--pure-python-tkrzw-実装)
8. [core/upstream — 上流コードの隔離](#8-coreupstream--上流コードの隔離)
9. [core/normalizer — 正規化・言語判定](#9-corenormalizer--正規化言語判定)
10. [core/db — 辞書アクセス層](#10-coredb--辞書アクセス層)
11. [core/scorer — スコアリング](#11-corescorer--スコアリング)
12. [core/query — クエリエンジン](#12-corequery--クエリエンジン)
13. [pipelines — バッチ処理](#13-pipelines--バッチ処理)
14. [agent_api — エージェント連携](#14-agent_api--エージェント連携)
15. [cli — コマンドライン](#15-cli--コマンドライン)
16. [入出力データ仕様](#16-入出力データ仕様)
17. [出力フォーマット仕様（参考語彙）](#17-出力フォーマット仕様参考語彙)
18. [自動発火仕様](#18-自動発火仕様)
19. [性能仕様](#19-性能仕様)
20. [テスト仕様](#20-テスト仕様)
21. [既知の制限事項と今後の課題](#21-既知の制限事項と今後の課題)
22. [変更履歴](#22-変更履歴)
23. [付録A: 定数一覧](#付録a-定数一覧)
24. [付録B: HashDBM バイナリレイアウト](#付録b-hashdbm-バイナリレイアウト)

---

## 1. 目的と適用範囲

### 1.1 目的

本書は、上位仕様書が定義する「AIエージェント向け語彙サイドカー」の**実装仕様**を、実装済みコードの挙動に即して詳細に記述する。上位仕様書が「何を実現するか」を定めるのに対し、本書は「どのように実現されているか」を、再実装・保守・検証に足る粒度で定める。

### 1.2 適用範囲

- **読み取り専用**の語彙サイドカーである。辞書データの構築・更新は対象外。
- **Pure Python** で実装され、C++拡張を必要としない。
- 対象データは統合英和辞書（`tkrzw-dict`）の配布データである。
- 想定利用形態は、LLMエージェントからのツール呼び出し、CLI、HTTP API の3つである。

### 1.3 上位仕様書との対応

| 上位仕様書の節 | 本書の対応節 |
|---|---|
| 4. システム構成 | 5, 7–15 |
| 5. データ構造 | 16, 付録B |
| 6. コア機能 | 12 |
| 7. スコアリング | 11 |
| 8. AIエージェント連携 | 14, 18 |
| 9. 出力フォーマット | 17 |
| 10. CLI仕様 | 15 |
| 11. API仕様 | 14.4 |
| 12. パフォーマンス要件 | 19 |
| 13. ストレージ設計 | 6, 7 |
| 14. 拡張性 | 21 |

---

## 2. 用語定義

| 用語 | 定義 |
|---|---|
| **サイドカー** | LLM本体の外側で語彙理解を補助するモジュール。翻訳ではなく意味候補を提示する。 |
| **見出し語 (headword)** | 辞書のキーとなる英語語。`entry["word"]`。 |
| **エントリ (entry)** | 見出し語に対応する辞書レコード。語義 `item` のリスト、訳語 `translation`、関連語 `related`、共起語 `cooccurrence` 等を含む。 |
| **語義 (sense)** | エントリ内の `item` 要素。品詞 `pos` と英語定義文 `text` を持つ。 |
| **訳語 (translation)** | 日本語の訳語。エントリ単位でコーパス頻度順に並ぶ。 |
| **候補 (candidate)** | エージェントに提示する日本語語義候補。 |
| **HashDBM (.tkh)** | Tkrzwのファイルハッシュデータベース形式。 |
| **コンテキスト** | 対象語が出現する文または段落。語義の再順位付けに用いる。 |
| **コンテキスト特徴量** | コンテキスト中の単語を重み付きで表した疎ベクトル。 |
| **自動発火** | エージェントが明示的に呼ばずともサイドカーを起動すべき条件。 |

---

## 3. システム概要

### 3.1 設計方針

1. **読み取り専用**: 辞書データは改変しない。すべてのDBは `ACCESS_READ` でメモリマップする。
2. **Pure Python**: Tkrzw C++拡張を排除し、`.tkh` 形式を直接読む自己完結実装 `pytkrzw` を持つ。
3. **上流の再利用**: `tkrzw-dict` の検索エンジン（注釈処理・屈折解決・共起展開）はそのまま利用し、依存を `core/upstream.py` に隔離する。
4. **トランスポート非依存**: ツール実装 `handler.py` はHTTP/CLI/プロセス内のいずれからも利用できる。
5. **下位互換**: `pytkrzw` は `tkrzw` C++バインディングのAPI部分集合を同一シグネチャで提供し、上流コードの変更を `import` 1行に留める。

### 3.2 アーキテクチャ

```
LLMエージェント
   │  (1) ツール呼び出し vocabulary_support
   ▼
agent_api/handler.py ── CLI (cli/main.py) ── HTTP (agent_api/router.py)
   │
   ▼
pipelines/{enrich, extract_terms}.py      バッチ処理・整形・自動発火判定
   │
   ▼
core/query.py   VocabularyQuery  (lookup / resolve / enrich / extract_terms)
   │            ├─ core/scorer.py   Scorer (スコアリング・分野ラベル)
   │            ├─ core/normalizer.py  正規化・言語判定・語抽出
   │            └─ core/db.py      Dictionary (辞書アクセス)
   │
   ▼
core/upstream.py ── tkrzw-dict の UnionSearcher / tkrzw_dict
   │
   ▼
core/pytkrzw.py (DBM / File / Utility)  ──  mmap  ──  union-*.tkh / union-keys.txt
```

### 3.3 データフロー（enrich の場合）

1. `handler.handle({"text": ...})` が引数を検証する。
2. `pipelines.enrich.enrich_text` が `VocabularyQuery` を生成する。
3. `query.enrich` が上流の `AnnotateText` でテキストを語句スパンに分割し、辞書収録語のみを候補として抽出する。
4. 同じ `enrich` が、候補語**以外**の文脈語からコンテキスト特徴量を構築する（関連語展開を含む）。
5. 各候補語について `_rank_meanings` が語義を再順位付けする。
6. `pipelines.enrich.format_vocabulary_hints` が `[参考語彙]` ブロックに整形する。

### 3.4 公開ファサード `VocabularySidecar`

パッケージ直下の `__init__.py` が提供する最も簡便な入口。辞書を1つ開き、4操作を委譲する。

| メンバ | 仕様 |
|---|---|
| `VocabularySidecar(data_prefix=None)` | 生成時に辞書を開く |
| `__enter__` / `__exit__` | コンテキストマネージャ |
| `dictionary` (property) | 内部の `Dictionary` |
| `close()` | 辞書を閉じる |
| `lookup(word)` | `VocabularyQuery.lookup` へ委譲 |
| `resolve(word, context="")` | `VocabularyQuery.resolve` へ委譲 |
| `enrich(text)` | `VocabularyQuery.enrich` へ委譲 |
| `extract_terms(text)` | `VocabularyQuery.extract_terms` へ委譲 |

```python
from tkrzw_dict_agent import VocabularySidecar

with VocabularySidecar() as sidecar:
    sidecar.lookup("tightening")
    sidecar.resolve("bank", "The river bank was eroded by the flood.")
```

長命なサービスでは `agent_api.VocabularySupportHandler` または `core.open_dictionary` を直接使う方が、辞書の再利用を明示できる。

---

## 4. 実行環境と依存関係

### 4.1 必須環境

| 項目 | 要件 |
|---|---|
| OS | UNIX系（Linux で検証） |
| Python | 3.9 以上（3.12.3 で検証） |
| 必須ライブラリ | `regex`（上流 `tkrzw_dict` が使用） |

### 4.2 オプション環境

| extra | パッケージ | 用途 |
|---|---|---|
| `api` | `fastapi`, `uvicorn`, `httpx` | HTTP API とそのテスト |
| `dev` | `pytest` | テスト実行 |

### 4.3 本プロジェクトが排除した依存

| 従来の依存 | 排除方法 |
|---|---|
| Tkrzw C++ ライブラリ (`libtkrzw.so`) | 不要（`pytkrzw` が直接読む） |
| `tkrzw` Python 拡張 | 不要（上流コードの `import` を差し替え） |
| NLTK / MeCab | 実行時は不要（辞書構築時のみ） |

**注意**: 実行時に `LD_LIBRARY_PATH` の設定は不要である。

### 4.4 インストール手順

```shell
# 1) リポジトリとデータの取得（初回のみ）
git clone https://github.com/estraier/tkrzw-dict.git
curl -L -o union-dict-data.tar.gz https://dbmx.net/dict/union-dict-data.tar.gz
tar xzf union-dict-data.tar.gz -C tkrzw-dict/

# 2) Python 環境
python3 -m venv .venv
.venv/bin/pip install -e .            # 必須（regex のみ）
.venv/bin/pip install -e '.[api]'     # HTTP API を使う場合
.venv/bin/pip install -e '.[dev]'     # テストを実行する場合
```

---

## 5. ディレクトリ構成

```
<repo root>/
  ├ README.md                             プロジェクト概要・上流との関係
  ├ LICENSE                               Apache License 2.0
  ├ NOTICE                                上流への帰属・データライセンス
  ├ pyproject.toml                        パッケージ定義・console script
  ├ .gitignore                            辞書データ・venv・上流チェックアウトの除外
  ├ docs/spec/                            仕様書
  ├ patches/                              上流への改変パッチ
  ├ scripts/setup.sh                      環境再現スクリプト
  ├ tkrzw-dict/                           上流コード（vendored、改変は import 1行のみ）
  │   ├ tkrzw_union_searcher.py           検索エンジン
  │   ├ tkrzw_dict.py                     正規化・頻度ユーティリティ
  │   ├ union-body.tkh                    英和辞書本体（582.4 MiB）
  │   ├ union-tran-index.tkh              和英索引（121.1 MiB）
  │   ├ union-infl-index.tkh              屈折索引（12.8 MiB）
  │   ├ union-keys.txt                    英和キー一覧（6.3 MiB）
  │   ├ union-tran-keys.txt               和英キー一覧（17.8 MiB）
  │   └ union-examples.tsv                例文対訳（152.1 MiB）
  ├ tkrzw_dict_agent/                     本パッケージ
  │   ├ __init__.py                       VocabularySidecar（公開ファサード）
  │   ├ core/
  │   │   ├ __init__.py
  │   │   ├ pytkrzw.py                    Pure Python Tkrzw
  │   │   ├ upstream.py                   上流 import の隔離
  │   │   ├ normalizer.py                 正規化・言語判定・語抽出
  │   │   ├ db.py                         Dictionary（辞書アクセス）
  │   │   ├ scorer.py                     スコアリング・分野ラベル
  │   │   └ query.py                      VocabularyQuery
  │   ├ pipelines/
  │   │   ├ enrich.py                     バッチ enrich・整形
  │   │   └ extract_terms.py              重要語抽出・自動発火
  │   ├ agent_api/
  │   │   ├ tool_schema.json              ツール定義
  │   │   ├ handler.py                    ツール実装
  │   │   ├ router.py                     FastAPI ルータ
  │   │   ├ server.py                     常駐サーバ起動（0.0.0.0:8765）
  │   │   └ __main__.py                   python -m 用
  │   └ cli/
  │       ├ main.py                       CLI 実装
  │       └ __main__.py                   python -m 用
  ├ skills/
  │   └ tkrzw-dict-vocabulary-support/
  │       └ tkrzw-dict_SKILL.md           他ホストのエージェント向けスキル定義
  └ tests/
      ├ test_pytkrzw.py                   Pure Python Tkrzw のテスト
      ├ test_core.py                      core 層のテスト
      ├ test_agent_api.py                 handler/pipelines のテスト
      ├ test_router.py                    HTTP API のテスト
      ├ test_server.py                    サーバ起動設定のテスト
      └ test_cli.py                       CLI のテスト
```

---

## 6. データ資産仕様

### 6.1 辞書データファイル

| ファイル | サイズ | 形式 | 用途 |
|---|---|---|---|
| `union-body.tkh` | 582.4 MiB | HashDBM | 見出し語 → 記事情報（JSON） |
| `union-tran-index.tkh` | 121.1 MiB | HashDBM | 日本語訳語 → 英語見出し語（TSV） |
| `union-infl-index.tkh` | 12.8 MiB | HashDBM | 屈折形 → 原形（TSV） |
| `union-keys.txt` | 6.3 MiB | テキスト | 英和キー一覧（頻度降順） |
| `union-tran-keys.txt` | 17.8 MiB | テキスト | 和英キー一覧（頻度降順） |
| `union-examples.tsv` | 152.1 MiB | TSV | 見出し語・例文・対訳 |

### 6.2 実測値

| 項目 | 値 |
|---|---|
| `union-body.tkh` レコード数 | 499,750 |
| バケット数 | 999,521 |
| `offset_width` | 4 |
| `align_pow` | 0 |
| 圧縮モード | なし |
| CRCモード | なし |
| 更新モード | in-place（`static_flags = 0x01`） |
| データ構築日 | 2024-03-30 |
| 上流コミット | `be2be5252e8a1ec28cc566ae64cd98cf85ff09fb` |

データは同一ディレクトリに `union` という接頭辞で配置する。接頭辞は `--data-prefix` または環境変数 `TKRZW_DICT_PREFIX` で変更できる。

---

## 7. core/pytkrzw — Pure Python Tkrzw 実装

`pytkrzw` は Tkrzw C++バインディングの**読み取り専用部分集合**を Pure Python で実装する。形式仕様は Tkrzw 1.0.30 の C++ ソース（`tkrzw_dbm_hash.cc`、`tkrzw_dbm_hash_impl.cc`、`tkrzw_sys_config.h`、`tkrzw_hash_util.cc`）に基づく。

### 7.1 モジュール公開API

```python
__all__ = ["DBM", "File", "Status", "Utility"]
```

ステータスコード定数: `SUCCESS`, `NOT_FOUND_ERROR`, `SYSTEM_ERROR`, `UNEXPECTED_ERROR`, `BROKEN_DATA_ERROR`, `DUPLICATION_ERROR`, `INFEASIBLE_ERROR`, `PRECONDITION_ERROR`, `NOT_IMPLEMENTED_ERROR`, `INVALID_ARGUMENT_ERROR`

### 7.2 `Status` クラス

操作結果。`tkrzw.Status` 互換。

| メソッド | 仕様 |
|---|---|
| `Status(code=SUCCESS, message="")` | 生成 |
| `GetCode()` | コード文字列を返す |
| `GetMessage()` | メッセージを返す |
| `IsOK()` | `SUCCESS` なら `True` |
| `OrDie()` | 非 `SUCCESS` なら `RuntimeError` を送出、それ以外は `None` |
| `__bool__()` | `SUCCESS` なら `True` |
| `__eq__(other)` | コードの一致で比較 |

属性 `code_`、`message_` は可変であり、`DBM.Get(key, status)` が直接書き換える。

### 7.3 `DBM` クラス（HashDBM 読み取り）

#### 7.3.1 メソッド一覧

| メソッド | 仕様 |
|---|---|
| `DBM()` | 未オープン状態で生成 |
| `Open(path, writable=False, dbm="HashDBM", **kwargs)` | 読み取り専用で開く。`Status` を返す |
| `Close()` | mmap とファイルを解放。`Status` を返す |
| `Get(key, status=None)` | 値を `bytes` で返す。無ければ `None` |
| `GetStr(key, status=None)` | 値を `str`（UTF-8）で返す。無ければ `None` |
| `Count()` | レコード数を返す |
| `GetFileSize()` | ファイルサイズ（バイト）を返す |
| `GetPath()` | パスを返す |
| `__contains__(key)` | 収録の有無を返す（上流 `CheckExact` 用） |
| `__del__()` | 未クローズ時に解放（例外は無視） |

#### 7.3.2 `Open` の仕様

- `writable=True` → `NotImplementedError`
- `dbm != "HashDBM"` → `NotImplementedError`
- 既にオープン済み → `RuntimeError`
- メタデータ検証:
  - ファイルサイズ < 128 → `BROKEN_DATA_ERROR`
  - マジック `b"TkrzwHDB\n"` 不一致 → `BROKEN_DATA_ERROR`
  - `static_flags` の更新モードビットが 0 → `BROKEN_DATA_ERROR`
  - 圧縮モード ≠ 0 → `NOT_IMPLEMENTED_ERROR`
  - `offset_width` が 3–6 の範囲外 → `BROKEN_DATA_ERROR`
  - `num_buckets` < 1 → `BROKEN_DATA_ERROR`
- 失敗時はファイルを閉じ、未オープン状態に戻す。

#### 7.3.3 `Get` の仕様

1. キーが `str` なら UTF-8 で `bytes` 化する。
2. バケット索引 `i = PrimaryHash(key, num_buckets)` を計算する。
3. バケット値 `= ReadFixNum(meta + 128 + i*offset_width) << align_pow`。
4. 連鎖を辿り、最初にキーが一致したレコードで確定する。
   - 操作種別が `SET`/`ADD` → 値を返す
   - 操作種別が `REMOVE`/`VOID` → `None`（削除済み）
   - キー不一致 → 子オフセットへ
5. 連鎖終端（オフセット 0）→ `None`。

この「最初の一致で確定」は、in-place モードの連鎖順（バケット先頭が最新）に一致する。

#### 7.3.4 `PrimaryHash` の仕様

```
hash = MurmurHash2-64A(key, seed=19780211)
if num_buckets <= 0xFFFFFFFF:
    hash = (((hash & 0xFFFF000000000000) >> 48) | ((hash & 0x0000FFFF00000000) >> 16))
         ^ (((hash & 0x000000000000FFFF) << 16) | ((hash & 0x00000000FFFF0000) >> 16))
return hash % num_buckets
```

`MurmurHash2-64A` は `mul = 0xC6A4A7935BD1E995`、`rtt = 47`。8バイトブロック単位で処理し、端数は小さい側から XOR する。C++実装（`tkrzw::HashMurmur`）と全入力で一致することを検証済み。

#### 7.3.5 レコード読み取りの仕様

レコード先頭から `min(128, file_size - offset)` バイトを読み、以下を順に解釈する。

| フィールド | 幅 | 備考 |
|---|---|---|
| マジック | 1 byte | 上位2ビット=操作種別、下位6ビット=チェックサム（3未満は破損） |
| 子オフセット | `offset_width` bytes | ビッグエンディアン、`<< align_pow` |
| キー長 | varint | バイトデルタ符号化 |
| 値長 | varint | 同上 |
| パディング長 | varint | 同上 |
| CRC | `crc_width` bytes | 本データでは 0 |
| キー | `key_size` bytes | |
| 値 | `value_size` bytes | |

varint は上位ビット継続方式（7ビット/バイト）: `num = (num << 7) + (c & 0x7f)`、`c < 0x80` で終端。

操作種別: `VOID=0xC0`、`SET=0x80`、`REMOVE=0x40`、`ADD=0x00`。

### 7.4 `File` クラス（テキスト検索）

#### 7.4.1 メソッド一覧

| メソッド | 仕様 |
|---|---|
| `File()` | 生成 |
| `Open(path, writable=False, **kwargs)` | 読み取り専用で開く |
| `Close()` | 閉じる |
| `GetPath()` | パスを返す |
| `Search(mode, pattern, capacity=0)` | 一致する行を `list[str]` で返す |

`capacity=0` は無制限。行末の `\n` のみを除去する（`\r` は残す＝C++実装と同一）。

#### 7.4.2 検索モード

| mode | 一致条件 |
|---|---|
| `contain` | `pattern in line` |
| `begin` | `line.startswith(pattern)` |
| `end` | `line.endswith(pattern)` |
| `regex` | `re.search(pattern, line)`（部分一致） |
| `edit` | 編集距離の小さい上位 `capacity` 行 |
| `editbin` | `edit` のバイト版 |
| `containcase` | 大文字小文字を無視した `contain` |
| `containword` | 単語境界を考慮した `contain` |
| `containcaseword` | `containword` の大小無視版 |
| `contain*` | `\n` 区切りの複数パターンのいずれかを含む |
| `containcase*` | `contain*` の大小無視版 |
| `containword*` | `\n` 区切りの複数パターンのいずれかに単語一致 |
| `containcaseword*` | `containword*` の大小無視版 |

未知のモードは `RuntimeError`。

#### 7.4.3 単語境界の仕様（`StrWordSearch` 相当）

ある位置の一致が単語一致となるのは、次の両方を満たすとき。

- 「直前文字が英数字 **かつ** パターン先頭文字が英数字」でない
- 「直後文字が英数字 **かつ** パターン末尾文字が英数字」でない

英数字判定は C ロケール（`0-9A-Za-z`）。大小無視版は ASCII の `A-Z` のみを小文字化する（`str.lower()` は使わない）。

#### 7.4.4 `edit` モードの仕様

- 編集距離は UCS-4 コードポイント空間（Python の `str` と等価）で計算する。
- 上位 `capacity` 件を `(距離, 行内容)` の昇順で返す。
- 境界付き最大ヒープを用いる。ヒープが満杯のとき、`|len(行) - len(パターン)| > 現最悪距離` の行は距離が改善し得ないため走査を省略する。

### 7.5 `Utility` クラス

| メソッド | 仕様 |
|---|---|
| `EditDistanceLev(a, b)` | 2文字列（コードポイント空間）の Levenshtein 距離 |
| `PrimaryHash(data, num_buckets=0)` | `data`（`str` は UTF-8 化）のハッシュ。`num_buckets=0` は `UINT64MAX`（折り畳みなし） |
| `GetMemoryUsage()` | プロセスの最大 RSS（バイト）。`ru_maxrss * 1024` |

### 7.6 制限事項

| 項目 | 挙動 |
|---|---|
| 書き込み | 非対応（`NotImplementedError`） |
| 圧縮レコード（zlib/zstd/lz4/lzma/rc4/aes） | 非対応（`Open` が `NOT_IMPLEMENTED_ERROR`） |
| CRC付きレコード | ヘッダの `crc_width` を読み飛ばすのみ（検証はしない） |
| TreeDBM / SkipDBM 等 | 非対応 |

### 7.7 C++実装との等価性検証

| 検証項目 | 結果 |
|---|---|
| MurmurHash（空・1文字・8バイト境界・マルチブロック・UTF-8） | 完全一致 |
| 3DB × 5,006キーの `GetStr` | 完全一致（不一致 0） |
| `File.Search` 全モード | 完全一致 |
| `EditDistanceLev` 2,508ペア | 完全一致 |
| `search_union.py` 全40ケースの出力 | バイト単位で完全一致 |

---

## 8. core/upstream — 上流コードの隔離

上流 `tkrzw-dict` への依存を1箇所に集約する。

### 8.1 仕様

- モジュール読み込み時に `tkrzw-dict` ディレクトリを `sys.path` の先頭に追加する。
- ディレクトリはパッケージ位置から解決する（`<repo>/tkrzw-dict`）。
- 環境変数 `TKRZW_DICT_DIR` で上書きできる。
- 再エクスポート: `tkrzw_dict`、`UnionSearcher`。

| 関数 | 仕様 |
|---|---|
| `get_dict_dir()` | 辞書ディレクトリを返す |
| `is_available()` | `tkrzw_union_searcher.py` の存在を返す |

### 8.2 上流への改変

上流コードへの改変は次の2行のみ。

- `tkrzw_union_searcher.py`: `import tkrzw` → `import tkrzw_dict_agent.core.pytkrzw as tkrzw`
- `tkrzw_dict.py`: 同上

---

## 9. core/normalizer — 正規化・言語判定

| 関数 | 仕様 |
|---|---|
| `normalize_word(text)` | 辞書キー正規化。上流 `NormalizeWord` に委譲（空白圧縮・中黒除去・ダイアクリティック除去・小文字化・strip） |
| `remove_diacritic(text)` | 上流 `RemoveDiacritic` に委譲 |
| `predict_language(text)` | `"en"` / `"ja"` / `"other"` |
| `english_ratio(text)` | 英数字中のラテン文字・数字の比率 |
| `is_english_heavy(text)` | `predict_language == "en"` または `english_ratio > 0.30` |
| `extract_words(text)` | ラテン語トークンの `(表層, 開始オフセット)` リスト |
| `split_sentences(text)` | 文分割（`。.!?！？\n` を区切り、終端は保持） |
| `is_stop_word(language, word)` | 機能語判定 |
| `is_numeric_word(word)` | 数値語判定 |
| `normalize_word_for_lookup(text)` | `normalize_word` の別名 |

### 9.1 言語判定

```
kana + han > latin  → "ja"
latin > 0           → "en"
それ以外            → "other"
```

### 9.2 語抽出パターン

`[\p{Latin}\d][-_'’\p{Latin}\d]*`（先頭はラテン文字または数字、内部にハイフン・アポストロフィを許容）。上流 `UnionSearcher.re_latin_word` と同一。

### 9.3 ストップワード

上流の判定（数字・日本語機能語）に加え、本モジュール独自の**英語機能語集合**（約190語）を先に適用する。上流の集合は代名詞と一部助動詞のみであり、`of`・`in`・`to` などの前置詞が漏れるため、サイドカーの用途では不十分である。

---

## 10. core/db — 辞書アクセス層

### 10.1 `Dictionary` クラス

| メソッド | 仕様 |
|---|---|
| `Dictionary(data_prefix, capacity=100)` | 生成（未オープン） |
| `open()` | 上流 `UnionSearcher` を生成し、キーファイルを開く。失敗時 `DictionaryError` |
| `close()` | 解放 |
| `is_open()` | オープン状態 |
| `data_prefix` (property) | 接頭辞 |
| `lookup(word)` | 完全一致。`SearchExact` の結果（エントリのリスト） |
| `has_word(word)` | 収録有無。`CheckExact`（JSON 非デシリアライズ） |
| `lookup_raw(word)` | 正規化キーの生 JSON 文字列 |
| `resolve_inflection(word)` | 屈折形 → 原形リスト |
| `lookup_with_inflection(word)` | 完全一致、無ければ原形で再試行 |
| `search_context(core, prefix, suffix, capacity=None)` | 文脈付き語句検索 |
| `search_reverse(japanese, capacity=None)` | 和英逆引き |
| `annotate(text)` | `(スパン文字列, 単語か, エントリ列)` のリスト |
| `word_probability(entry)` | `probability` を `float` 化 |

| 関数 | 仕様 |
|---|---|
| `open_dictionary(data_prefix=None, capacity=100)` | 開いた `Dictionary` を返す。接頭辞省略時は同梱の `union` |
| `serialize_entry(entry, limit=None)` | API 応答用の簡約表現 |

`DictionaryError` は `RuntimeError` の派生。

### 10.2 データ接頭辞の解決

`open_dictionary` の接頭辞解決順:

1. 引数 `data_prefix`
2. 環境変数 `TKRZW_DICT_PREFIX`（接頭辞そのもの。例 `/data/union`）
3. 環境変数 `TKRZW_DICT_DIR` 配下の `union`（辞書ディレクトリ。例 `/data` → `/data/union`）
4. 同梱の `<repo>/tkrzw-dict/union`

CLI（`--data-prefix`）、サーバ（`--data-prefix`）、`open_dictionary` はいずれも同じ解決を共有する。

---

## 11. core/scorer — スコアリング

### 11.1 スコア式（上位仕様書 第7章）

```
score = W_base × base_frequency
      + W_domain × domain_match
      + W_context × context_similarity
```

| 重み定数 | 値 |
|---|---|
| `WEIGHT_BASE_FREQUENCY` | 0.30 |
| `WEIGHT_DOMAIN_MATCH` | 0.30 |
| `WEIGHT_CONTEXT_SIMILARITY` | 0.40 |

各成分は `[0, 1]` に正規化され、`score` も `[0, 1]` に収まる。

### 11.2 `base_frequency(entry)`

エントリのコーパス出現確率 `probability` を対数尺度で `[0, 1]` に写像する。

```
log_prob = log10(probability)
value    = (log_prob - (-7.0)) / 5.0     # clamp [0, 1]
```

確率 `1e-2` → 1.0、`1e-7` → 0.0。頻出語ほど高い。

### 11.3 `domain_match(entry, context_words)`

コンテキスト語と、エントリの `cooccurrence`／`related`（各先頭24語）の重複数による。

```
overlap = |エントリのドメイン証拠 ∩ コンテキスト語|
overlap == 0 → 0.0
それ以外     → min(1.0, 0.4 × overlap + 0.2)
```

共起語は語義が属する領域の最も強い信号である、という設計判断に基づく。

### 11.4 `context_similarity(entry, context_features)`

エントリの特徴ベクトルとコンテキスト特徴量のコサイン類似度。

エントリ特徴量 `build_entry_features(entry)`:

- 見出し語: 重み 1.0
- `related`（先頭16語）: 0.95 の減衰系列
- `cooccurrence`（先頭20語）: 同上（下限 0.4）

正規化には**高速正規化 `fast_normalize`** を用いる。上流 `GetFeatures` は1文字ごとに正規表現を実行するため、バッチ処理では約2桁遅い。

### 11.5 `guess_domain(entry, context_words=None)`

出力用の日本語分野ラベルを返す。

- 証拠は**見出し語と英語語義定義文**（先頭12語義・各400文字）である。`related`／`cooccurrence` は広範で無関係な分野を引き込むため用いない（例: `policy` の関連語に法律語が並ぶが、定義文は汎用的）。
- 見出し語のヒットは重み 3、定義文のヒットは重み 1。
- 最高スコアが 2 未満なら `一般`。

| 分野キー | 日本語ラベル |
|---|---|
| `economics` | 経済 |
| `medicine` | 医療 |
| `technology` | IT |
| `law` | 法律 |
| `science` | 科学 |
| `general` | 一般 |

各分野の語彙集は 32–48 語。技術分野は `device`・`memory`・`system` などの汎用語を除外し、誤判定を防ぐ（33語）。

### 11.6 `rank_entries(entries, context_features, context_words, limit=None)`

`score_meaning` の降順で `(entry, score)` を返す。

---

## 12. core/query — クエリエンジン

### 12.1 `VocabularyQuery` クラス

| メソッド | 上位仕様書 |
|---|---|
| `VocabularyQuery(dictionary, scorer=None)` | 生成。分野・語義ベクトルのキャッシュ（各2048件）を保持 |
| `lookup(word)` | 6.1 |
| `resolve(word, context="", max_meanings=8)` | 6.2 |
| `enrich(text, max_terms=20)` | 6.3 |
| `extract_terms(text, max_terms=20)` | 6.4 |

### 12.2 `lookup(word)`

戻り値:

```json
{
  "word": "tightening",
  "found": true,
  "pronunciation": "ˈtaɪtənɪŋ",
  "translations": ["締めつけ", "締付", "引き締め", "..."],
  "related": ["clamping", "belt-tightening", "..."],
  "probability": 1.85e-05,
  "lemmas": []
}
```

未収録時は `found: false`、`translations`／`related` は空、`lemmas` に屈折解決の候補を返す。

### 12.3 `resolve(word, context, max_meanings)`

#### 12.3.1 アルゴリズム

1. `lookup_with_inflection(word)` でエントリを得る。無ければ `[]`。
2. 対象語（および原形）を**展開除外集合**としてコンテキスト特徴量を構築する。
3. `rank_entries` でエントリを文脈順に並べる。
4. 各エントリについて:
   a. 分野ラベルをキャッシュから取得する。
   b. 語義ベクトル（キャッシュ）を取得する。
   c. 各訳語について `_translation_context_similarity` を計算する。
   d. 訳語の重みを次式で決める。

```
max_similarity > 0 のとき:
    weight = 0.4 × order + 0.6 × (similarity / max_similarity)
それ以外:
    weight = order
order は訳語順の減衰（初項 1.0、公比 0.94）
```

   e. `weight` の降順に訳語を並べ、`score = entry_score × weight` を `[0, 1]` にクランプする。

#### 12.3.2 設計根拠

- 訳語はエントリ内でコーパス頻度順に並ぶが、頻度順は文脈を反映しない。`bank` の「銀行」と「土手」のように1レコードに複数語義が同居する場合、文脈で逆転させる必要がある。
- 頻度事前分布と文脈信号を**積**でなく**重み付き和**とするのは、頻度差（0.94ⁿ）が文脈差より大きくなりすぎるのを防ぐためである。
- `similarity` を最大値で**相対正規化**するのは、エントリ間で尺度を揃えるためである。

### 12.4 コンテキスト特徴量の構築

`build_context_features(dictionary, context, max_words=64, expand=True,
expansion_seeds=8, expansion_terms=8, exclude=None)`

1. コンテキスト中のラテン語のうち、機能語・短語を除き、辞書収録語のみを基本特徴量とする（重みは出現回数）。
2. `expand=True` のとき、`exclude` に含まれない先頭 `expansion_seeds` 語について、その `related`（先頭 `expansion_terms` 語）を重み **0.5** で追加する。
3. 合計 `max_words` で打ち切る。

**展開の意義**: 「the river bank」は、`river` が `riverbank`・`riverside`・`watercourse` を連れてくることで初めて `bank` の川岸語義定義と一致する。

**除外の意義**: 対象語自身を展開すると、その語の**全語義**の関連語が混入し、どの語義も等しく関連して見えてしまう。`resolve` は対象語を、`enrich` は全候補語を除外する。

### 12.5 `_translation_context_similarity`

```
メモ: context_features は「単語 → 重み」、sense_vectors は (定義文, 単語集合) のリスト

対象訳語を含む定義文ごとに:
    overlap = Σ_{w ∈ 定義文の単語集合} context_features[w]
    similarity = overlap / (||context_features||₂ × √|定義文の単語集合|)
最大値を返す（[0, 1] にクランプ）
```

定義文長で正規化するのは、`bank` のように定義文の長さが極端に異なるエントリで、長い定義文が常に勝つのを防ぐためである。

### 12.6 `enrich(text, max_terms)`

1. 上流 `AnnotateText` でスパン分割し、辞書収録語スパンのみを候補とする（重複除去・機能語除外）。
2. 候補語集合を `exclude` としてコンテキスト特徴量を1回だけ構築する（候補ごとの再計算を避ける）。
3. 各候補について `_rank_meanings` を適用する。
4. 戻り値は `{"terms": [{"word", "candidates", "meanings"}]}`。

`candidates` は訳語文字列のリスト（上位仕様書 6.3 準拠）、`meanings` は分野・スコアを含む詳細である。

### 12.7 `extract_terms(text, max_terms)`

1. ラテン語トークンを抽出し、機能語・数値語・2文字未満を除外する。
2. 文頭以外の大文字始まりを固有名詞とし、重みに `PROPER_NOUN_BONUS = 1.5` を乗じる。
3. TF-IDF を計算する。

```
weight = (出現回数 / 総トークン数)
idf    = clamp((log10(probability) + 7.0) / 2.0, 0, 1)   # 辞書未収録語は除外
score  = weight × idf
```

4. `score` の降順で最大 `max_terms` 語を返す。

---

## 13. pipelines — バッチ処理

### 13.1 `pipelines/enrich.py`

| 名前 | 仕様 |
|---|---|
| `Enrichment(text, terms)` | 結果保持。`words`、`candidates(word)`、`to_dict()` |
| `enrich_text(text, dictionary=None, max_terms=20)` | `Enrichment` を返す。辞書省略時は同梱辞書を開いて閉じる |
| `format_vocabulary_hints(enrichment, max_candidates=8)` | `[参考語彙]` ブロックを生成（17章） |
| `should_enrich(text)` | 英語主体なら `True` |

### 13.2 `pipelines/extract_terms.py`

| 名前 | 仕様 |
|---|---|
| `extract_document_terms(text, dictionary=None, max_terms=20, min_score=0.0)` | 文単位で抽出し、初出順に統合 |
| `unknown_word_ratio(text, dictionary)` | 辞書未収録語の比率（重複除去後の語数で計算） |
| `detect_domains(text)` | 分野語彙集との一致率が `0.15` 以上の分野名リスト |
| `should_fire(text, dictionary)` | 自動発火判定（18章） |

---

## 14. agent_api — エージェント連携

### 14.1 `tool_schema.json`

```json
{
  "name": "vocabulary_support",
  "description": "Provide Japanese hints for difficult English words. ...",
  "parameters": {
    "type": "object",
    "properties": {
      "text":      {"type": "string",  "description": "..."},
      "word":      {"type": "string",  "description": "..."},
      "max_terms": {"type": "integer", "minimum": 1, "maximum": 100}
    },
    "required": ["text"]
  }
}
```

`word` 指定時は対象語のみを解決する。`max_terms` は 1–100 にクランプされる（既定 20）。

### 14.2 `handler.py`

| 名前 | 仕様 |
|---|---|
| `TOOL_NAME` | `"vocabulary_support"` |
| `load_tool_schema()` | スキーマを `dict` で返す |
| `VocabularySupportHandler(dictionary=None)` | 生成。辞書省略時は初回に遅延オープン |
| `.handle(arguments)` | ツール呼び出し。`{"terms", "hint"}` を返す |
| `.handle_json(arguments_json)` | JSON文字列版 |
| `.close()` | 自前で開いた辞書を閉じる |

`handle` のエラー:

| 条件 | 例外 |
|---|---|
| `arguments` が辞書でない | `TypeError` |
| `text` 欠落・非文字列 | `ValueError` |

`hint` は 17章の `[参考語彙]` ブロック。候補が無い場合は空文字と `message` を返す。

### 14.3 `router.py`

| 名前 | 仕様 |
|---|---|
| `build_router(dictionary=None)` | `APIRouter` を返す |
| `create_app(dictionary=None)` | 単体起動用 `FastAPI` アプリ |
| `RouterState` | 共有辞書・クエリを保持 |

### 14.4 HTTP API 仕様（上位仕様書 第11章）

#### POST /lookup

リクエスト: `{"word": string, "context": string(任意)}`
レスポンス: `lookup()` の結果に `meanings`（上位8件）を追加した JSON。

#### POST /enrich

リクエスト: `{"text": string}`
レスポンス: `{"terms": [{"word", "candidates", "meanings"}]}`

#### POST /extract

リクエスト: `{"text": string}`
レスポンス: `{"terms": [string, ...]}`

#### GET /health

レスポンス: `{"status": "ok", "tool": "vocabulary_support"}`

#### 共通仕様

| 項目 | 値 |
|---|---|
| 本文形式 | JSON オブジェクト |
| 応答 Content-Type | `application/json; charset=utf-8` |
| 必須フィールド欠落 | 400 |
| 本文が非オブジェクト | 400 |
| 本文長 > 20,000 文字 | 413 |
| 既定 `max_meanings` / `max_terms` | 8 / 20 |

応答の Content-Type には `charset=utf-8` を明示する。JSON は規格上 UTF-8 であるが、生バイトを読み取って文字コードを推測するクライアント（PowerShell の `[Console]::OutputEncoding`、一部の HTTP ライブラリ）にとってヘッダの明示が有効である。実装は `build_router` が `default_response_class` に指定する `UTF8JSONResponse` が担う。

### 14.5 `server.py` — 常駐サーバ起動

他ホストから呼び出せるよう、既定で `0.0.0.0` にバインドする起動エントリを提供する。

| 名前 | 仕様 |
|---|---|
| `DEFAULT_HOST` | `"0.0.0.0"` |
| `DEFAULT_PORT` | `8765` |
| `APP_IMPORT_STRING` | `"tkrzw_dict_agent.agent_api.router:create_app"` |
| `build_parser()` | 引数パーサを返す |
| `main(argv=None)` | uvicorn を起動する。uvicorn 未導入時は終了コード 2 |

#### 14.5.1 オプション

| オプション | 既定 | 環境変数 | 仕様 |
|---|---|---|---|
| `--host` | `0.0.0.0` | `TKRZW_DICT_HOST` | バインドアドレス。既定で全インターフェース |
| `--port` | `8765` | `TKRZW_DICT_PORT` | バインドポート |
| `--workers` | 1 | – | ワーカープロセス数 |
| `--log-level` | `info` | – | uvicorn ログレベル |
| `--data-prefix` | 同梱辞書 | `TKRZW_DICT_PREFIX` | 辞書接頭辞。ワーカーへ環境変数で伝播する |

コマンドライン引数は環境変数より優先される。

#### 14.5.2 起動方法

```shell
# console script（0.0.0.0:8765）
.venv/bin/dict-server

# モジュール実行（同等）
.venv/bin/python3 -m tkrzw_dict_agent.agent_api

# ループバック限定にする場合
.venv/bin/dict-server --host 127.0.0.1

# ポート・ワーカー指定
.venv/bin/dict-server --port 9000 --workers 4
```

`create_app` を import 文字列で指定し `factory=True` で起動するため、`--workers`・`--reload` が機能する。辞書は各ワーカーの初回リクエスト時に遅延オープンされ、プロセス生存中はメモリマップを保持する。

#### 14.5.3 常駐運用

`dict-server` はフォアグラウンドで動作する常駐プロセスである。停止・再起動は OS のサービス管理（systemd 等）またはプロセス管理機構に委ねる。

systemd ユニット例:

```ini
[Unit]
Description=Tkrzw-Dict Vocabulary Sidecar
After=network.target

[Service]
Type=simple
WorkingDirectory=/path/to/tkrzw_dict_agent
ExecStart=/path/to/tkrzw_dict_agent/.venv/bin/dict-server --host 0.0.0.0 --port 8765
Restart=on-failure
User=svc-tkrzw

[Install]
WantedBy=multi-user.target
```

#### 14.5.4 セキュリティ上の注意

本 API は**認証・CORS・レート制限・TLS を持たない**。`0.0.0.0` バインドは、そのポートに到達できる全ホストへ機能を公開することを意味する。外部公開する場合は次のいずれかを講じること。

- リバースプロキシ（nginx 等）で TLS 終端・認証・レート制限を行う
- `--host 127.0.0.1` で起動し、SSH トンネル等で限定公開する
- ファイアウォールで送信元を制限する

### 14.6 他ホストのエージェント向けスキル定義

`skills/tkrzw-dict-vocabulary-support/SKILL.md` は、他ホストの LLM エージェントが本 API を利用するためのスキル定義である。内容は次のとおり。

| 項目 | 内容 |
|---|---|
| フロントマター | `name`、`description`（`Use when:` による発動条件を含む） |
| エンドポイント | ベース URL の解決、`GET /health` による疎通確認 |
| 操作仕様 | `/lookup`・`/enrich`・`/extract` のリクエスト／レスポンス、文脈指定の効果 |
| ツール定義 | 関数呼び出し用 `vocabulary_support` 定義（`tool_schema.json` と同一） |
| 利用指針 | 発動条件、結果の読み方（候補から選択する設計）、`candidates` を丸ごと出力しない注意 |
| 実装例 | curl、標準ライブラリ、`requests`、SSH 経由 |
| エラー仕様 | 400／404／413／5xx／接続失敗の扱い、タイムアウト、`Content-Type` |
| 制限事項 | 語彙重なりのみの語義判別、分野タグの粒度、認証なし |

スキルはディレクトリごとエージェントのスキル格納先へコピーして配布する。ベース URL は `TKRZW_DICT_API` 環境変数またはスキル本文の `<sidecar-host>` を運用環境のホスト名に置換して用いる。

---

## 15. cli — コマンドライン

### 15.1 コマンド（上位仕様書 第10章）

```shell
dict lookup tightening
dict enrich "Fed tightening policy"
dict extract "long text"
```

### 15.2 共通オプション

| オプション | 仕様 |
|---|---|
| `--data-prefix PATH` | 辞書データ接頭辞（環境変数 `TKRZW_DICT_PREFIX`） |
| `--json` | JSON 出力 |

`--json` と `--data-prefix` はサブコマンドの前後どちらでも指定できる（argparse の `SUPPRESS` 既定を利用）。

### 15.3 サブコマンド

| コマンド | 引数・オプション | 挙動 |
|---|---|---|
| `lookup` | `word`、`--context`、`--max-meanings` | 訳語候補をスコア付きで表示。未収録は終了コード 1 |
| `enrich` | `text`（`-` で標準入力）、`--max-terms` | `[参考語彙]` を表示 |
| `extract` | `text`（`-` で標準入力）、`--max-terms` | 重要語を1行1語で表示 |

### 15.4 起動方法

```shell
.venv/bin/dict lookup tightening          # console script
.venv/bin/python3 -m tkrzw_dict_agent.cli lookup tightening
```

### 15.5 標準入出力のエンコーディング契約

CLI は、**リダイレクトされた標準入出力を UTF-8 に固定する**（`configure_stdio_encoding()`）。

| 条件 | 挙動 |
|---|---|
| 標準出力が端末（TTY） | プラットフォーム既定のまま（Windows では PEP 528 が Unicode を描画） |
| 標準出力がパイプ・ファイル（非TTY） | UTF-8 に再設定 |
| 標準入力が非TTY | UTF-8 に再設定（`dict enrich -` の入力用） |
| `PYTHONIOENCODING` が設定済み | 再設定しない（利用者の指定を尊重） |

**根拠**: Python は標準出力がリダイレクトされるとロケール符号化（Windows では ANSI コードページ cp932／cp1252）で出力する。エージェントハーネスがこれを UTF-8 として解釈すると文字化けする（`締めつけ` → `ç· ãã¤ãã`）。リダイレクト時に UTF-8 を保証することで、「生バイトを取得して UTF-8 で復号する」という消費側の実装が正しく機能する。端末出力を変更しないのは、cp932 のコンソールへ UTF-8 バイトを書き込むと逆に文字化けするためである。

**消費者側の注意**: PowerShell はネイティブプログラムの出力を `[Console]::OutputEncoding` で復号する。Windows PowerShell 5.1 の既定は ANSI コードページであるため、`[Console]::OutputEncoding = [System.Text.Encoding]::UTF8`（および `chcp 65001`）を設定するか、HTTP API を用いること。詳細はスキル定義の「Windows and console encoding」を参照。

---

## 16. 入出力データ仕様

### 16.1 `lookup` の戻り値

```json
{
  "word": "tightening",
  "found": true,
  "pronunciation": "ˈtaɪtənɪŋ",
  "translations": ["締めつけ", "締付", "引き締め"],
  "related": ["clamping", "belt-tightening"],
  "probability": 1.85e-05,
  "lemmas": []
}
```

### 16.2 Meaning レコード

```json
{"ja": "引き締め", "domain": "経済", "score": 0.4481, "word": "tightening"}
```

`score` は小数第4位に丸める。

### 16.3 `enrich` の戻り値

```json
{
  "terms": [
    {
      "word": "tightening",
      "candidates": ["締めつけ", "締付", "引き締め"],
      "meanings": [ { "ja": "...", "domain": "...", "score": 0.0, "word": "..." } ]
    }
  ]
}
```

### 16.4 `extract_terms` の戻り値

```json
{"terms": ["inflation", "central", "bank", "tightening"]}
```

---

## 17. 出力フォーマット仕様（参考語彙）

`format_vocabulary_hints` は上位仕様書 第9章の形式を生成する。

```text
[参考語彙]
tightening:
- 金融引き締め（経済）
- 強化（一般）
```

- 先頭行は固定文字列 `[参考語彙]`。
- 語ごとに `見出し語:` の行を置き、`- 訳語（分野）` を候補順に並べる。
- 分野ラベルが空なら括弧を省略する。
- 候補は既定で最大8件。

**設計意図**: 単一の訳語ではなく候補を提示し、選択をエージェントに委ねる（上位仕様書「LLMに選択させる設計」）。

---

## 18. 自動発火仕様

`should_fire(text, dictionary)` は上位仕様書 8.3 の3条件を次のように解釈する。

```
英語主体でない                → False
未知語率 > 0.10               → True
いずれかの分野を検出          → True
それ以外                      → False
```

| 条件 | 閾値・定義 |
|---|---|
| 英語率 | `is_english_heavy`（ラテン系、または英数字比 > 0.30） |
| 未知語率 | `UNKNOWN_WORD_THRESHOLD = 0.10` |
| 専門分野検出 | `detect_domains` が1件以上（分野語彙一致率 ≥ `DOMAIN_RATIO_THRESHOLD = 0.15`） |

3条件は**選言**として扱う。未知語率のみを要求すると、辞書に収録済みだが小型LLMが苦手な語（`tightening` など）で発火せず、実用に合わないためである。

---

## 19. 性能仕様

### 19.1 上位仕様書の目標

| 項目 | 目標 |
|---|---|
| lookup | < 1 ms |
| enrich（1文） | < 10 ms |
| DBサイズ | 数GB対応 |

### 19.2 実測値（Python 3.12.3、辞書データ実測）

| 操作 | 中央値 | 備考 |
|---|---|---|
| `lookup` | **0.079 ms** | 目標の約1/13 |
| `resolve`（文脈付き） | **1.172 ms** | 関連語展開を含む |
| `enrich`（3語文） | **2.47 ms** | |
| `enrich`（6語文） | **5.85 ms** | |
| `enrich`（8語文） | **8.50 ms** | 目標内 |
| `extract_terms`（2文） | **1.75 ms** | |
| `pytkrzw.DBM.GetStr` | 4–26 µs | DB別 |

enrich のコストは概ね**約1 ms/語**であり、典型的な1文（6–10語）は目標内に収まる。

### 19.3 性能設計上の措置

| 措置 | 効果 |
|---|---|
| mmap によるゼロコピー参照 | 起動時の全読み込みを回避 |
| `has_word`（JSON 非デコード） | コンテキスト特徴量構築の高速化 |
| 高速正規化 `fast_normalize` | `GetFeatures` 比で約2桁高速 |
| 分野・語義ベクトルの語見出しキャッシュ（2048件） | enrich を 141 ms → 7 ms に短縮 |
| コンテキスト特徴量を enrich で1回のみ構築 | 候補ごとの再計算を回避 |
| 文脈展開のシード・語数制限 | 展開の計算量を有界化 |

### 19.4 低速な経路

`File.Search("edit", ...)` は全行走査のため、`union-keys.txt` に対して約10秒を要する。曖昧一致検索専用の経路であり、サイドカーの `lookup`／`resolve`／`enrich` はこの経路を用いない。

---

## 20. テスト仕様

### 20.1 テスト構成

| ファイル | 対象 | 件数 |
|---|---|---|
| `test_pytkrzw.py` | HashDBM 読み取り・ハッシュ・編集距離・行検索 | 18 |
| `test_core.py` | normalizer・scorer・Dictionary・query・ファサード | 29 |
| `test_agent_api.py` | tool schema・handler・pipelines・自動発火 | 15 |
| `test_router.py` | HTTP API・Content-Type charset（FastAPI 未導入時はスキップ） | 8 |
| `test_server.py` | サーバ起動設定・既定バインド・ASGI ターゲット | 7 |
| `test_cli.py` | CLI 引数解析・各コマンド・入出力エンコーディング | 13 |
| **合計** | | **90** |

### 20.2 実行方法

```shell
.venv/bin/python3 -m pytest tests/ -q
```

辞書データが無い場合、辞書依存のテストは `skip` される。`TKRZW_DICT_DIR` で辞書位置を指定できる。

### 20.3 主要な検証項目

- MurmurHash の既知値一致（C++版と同一）
- HashDBM ヘッダ検証とエラー処理
- 屈折解決（`ran` → `run`）
- `bank` の文脈による語義逆転（river文脈で「銀行」が先頭に来ないこと／money文脈で先頭に来ること）
- 分野ラベル（`doctor` → 医療、`server` → IT）
- 機能語が enrich／extract から除外されること
- 自動発火の3条件
- CLI の終了コードとオプション順序
- リダイレクトされた CLI 出力が UTF-8 バイト列であること（サブプロセスでバイト検証）
- サーバの既定バインドが `0.0.0.0` であること、環境変数と引数の優先順位、ASGI ファクトリの解決
- HTTP 応答の Content-Type が `charset=utf-8` を含むこと

---

## 21. 既知の制限事項と今後の課題

### 21.1 制限事項

| 項目 | 内容 |
|---|---|
| 語義判別の限界 | 語彙的な重なりのみで判別するため、文脈語と語義定義文に共通語が無い場合は頻度順に留まる。 |
| 分野ラベルの粒度 | 分野はエントリ単位で付与される。本来は語義単位が望ましい。1エントリに複数分野の語義が同居する場合、代表1分野となる。 |
| 分野の網羅性 | 分野語彙集は 経済・医療・IT・法律・科学・一般 の6種のみ。上位仕様書 14.2 が挙げる拡張には語彙集の追加が必要。 |
| `edit` 検索の速度 | 全行走査のため約10秒。 |
| 書き込み非対応 | 辞書の構築・更新は対象外。 |
| 圧縮DB非対応 | 圧縮レコードを持つDBは開けない。 |

### 21.2 今後の課題

1. **語義単位の分野付与**: `item` ごとに分野を判定し、訳語を語義に対応付ける。
2. **文脈展開の重み学習**: 現在は固定重み（展開 0.5、事前 0.4／文脈 0.6）。ログからの再学習（上位仕様書 第15章）。
3. **分野特化辞書**: 分野タグ付き辞書データの構築による精度向上。
4. **Wikipedia連携**: 記事からの語抽出と enrich の連携（上位仕様書 14.3）。
5. **`edit` 検索の最適化**: BK-tree 等による曖昧一致の高速化。
6. **不要語ブラックリスト**: ログに基づく除外語の運用（上位仕様書 第15章）。

---

## 22. 変更履歴

| 版 | 日付 | 内容 |
|---|---|---|
| 1.0 | 2026-09-20 | 初版。実装済みモジュールの詳細仕様を記述。 |
| 1.1 | 2026-09-20 | 他ホストから呼び出せる常駐サーバ（`dict-server`、既定 `0.0.0.0:8765`）を 14.5 に追加。データ接頭辞の解決順を明文化し、環境変数・console script 一覧を付録Aに追加。 |
| 1.2 | 2026-09-20 | 他ホストのエージェント向けスキル定義（`skills/tkrzw-dict-vocabulary-support/SKILL.md`）を 14.6 に追加。 |
| 1.3 | 2026-09-20 | Windows の文字化け対策。CLI のリダイレクト時 UTF-8 固定（15.5）と HTTP 応答の `charset=utf-8` 明示（14.4）を追加。スキル定義に「Windows and console encoding」を追加。 |
| 1.4 | 2026-09-20 | リポジトリ公開に伴い `LICENSE`（Apache-2.0）・`NOTICE`（上流帰属）・`README.md`（上流との関係）を追加。`pyproject.toml` にライセンス・URL メタデータを追加。 |

### 22.1 上位仕様書からの主な実装上の決定

| 項目 | 上位仕様書 | 本実装 |
|---|---|---|
| C++依存 | 言及なし | 完全排除（`pytkrzw` が `.tkh` を直接読む） |
| スコア式 | 3成分の和 | 各成分を `[0,1]` に正規化し重み付き和 |
| 語義再順位付け | 文脈ベース | 頻度事前 × 文脈信号の重み付き和、相対正規化 |
| 分野タグ | レコードに `domain` を想定 | 定義文から推定（データに存在しないため） |
| ストップワード | 言及なし | 英語機能語集合を独自定義 |
| 自動発火 | 3条件（関係不明） | 英語主体を前提に残り2条件を選言 |
| サーバ公開範囲 | 言及なし | 既定 `0.0.0.0`（他ホストから呼び出し可能） |

---

## 付録A: 定数一覧

### A.1 core/pytkrzw.py

| 定数 | 値 |
|---|---|
| `_META_SIZE` | 128 |
| `_META_MAGIC` | `b"TkrzwHDB\n"` |
| `_FBP_SECTION_SIZE` | 1008 |
| `_RECORD_BASE_HEADER_SIZE` | 16 |
| `_RECORD_BASE_ALIGN` | 4096 |
| `_MURMUR_SEED` | 19780211 |
| `_RECORD_MAGIC_VOID` | 0xC0 |
| `_RECORD_MAGIC_SET` | 0x80 |
| `_RECORD_MAGIC_REMOVE` | 0x40 |
| マジック下位6ビット最小値 | 3 |

### A.2 core/scorer.py

| 定数 | 値 |
|---|---|
| `WEIGHT_BASE_FREQUENCY` | 0.30 |
| `WEIGHT_DOMAIN_MATCH` | 0.30 |
| `WEIGHT_CONTEXT_SIMILARITY` | 0.40 |
| `_PROB_LOG_FLOOR` | -7.0 |
| `_PROB_LOG_SPAN` | 5.0 |
| `_DOMAIN_EVIDENCE_LIMIT` | 24 |
| `_HEADWORD_HIT_WEIGHT` | 3 |
| `_DOMAIN_ITEM_LIMIT` | 12 |
| `_DOMAIN_TEXT_LIMIT` | 400 |
| `_MIN_DOMAIN_SCORE` | 2 |
| `_FEATURE_DECAY` | 0.95 |
| `_FEATURE_RELATED_LIMIT` | 16 |
| `_FEATURE_COOCCURRENCE_LIMIT` | 20 |

### A.3 core/query.py

| 定数 | 値 |
|---|---|
| `DEFAULT_MAX_MEANINGS` | 8 |
| `DEFAULT_MAX_TERMS` | 20 |
| `TRANSLATION_ORDER_DECAY` | 0.94 |
| `TRANSLATION_PRIOR_WEIGHT` | 0.4 |
| `TRANSLATION_CONTEXT_WEIGHT` | 0.6 |
| `PROPER_NOUN_BONUS` | 1.5 |
| `_MIN_TERM_LENGTH` | 2 |
| `_SENSE_TEXT_LIMIT` | 320 |
| `_EXPANSION_WEIGHT` | 0.5 |
| キャッシュ容量 | 2048（分野・語義ベクトル各） |

### A.4 core/normalizer.py

| 定数 | 値 |
|---|---|
| `ENGLISH_RATIO_THRESHOLD` | 0.30 |
| 英語ストップワード | 186語 |

### A.5 pipelines

| 定数 | 値 |
|---|---|
| `UNKNOWN_WORD_THRESHOLD` | 0.10 |
| `DOMAIN_RATIO_THRESHOLD` | 0.15 |

### A.6 agent_api/router.py

| 定数 | 値 |
|---|---|
| `DEFAULT_MAX_MEANINGS` | 8 |
| `DEFAULT_MAX_TERMS` | 20 |
| `MAX_TEXT_LENGTH` | 20000 |

### A.7 agent_api/server.py

| 定数 | 値 |
|---|---|
| `DEFAULT_HOST` | `"0.0.0.0"` |
| `DEFAULT_PORT` | 8765 |
| `APP_IMPORT_STRING` | `"tkrzw_dict_agent.agent_api.router:create_app"` |

### A.8 環境変数一覧

| 変数 | 用途 | 参照箇所 |
|---|---|---|
| `TKRZW_DICT_DIR` | 辞書ディレクトリ | `core/upstream.py`, `core/db.py` |
| `TKRZW_DICT_PREFIX` | 辞書データ接頭辞（優先） | `core/db.py`, `cli/main.py`, `agent_api/server.py` |
| `TKRZW_DICT_HOST` | サーバのバインドアドレス | `agent_api/server.py` |
| `TKRZW_DICT_PORT` | サーバのバインドポート | `agent_api/server.py` |

### A.9 console script

| 名前 | エントリポイント | 用途 |
|---|---|---|
| `dict` | `tkrzw_dict_agent.cli.main:main` | コマンドライン操作 |
| `dict-server` | `tkrzw_dict_agent.agent_api.server:main` | 常駐 HTTP サーバ（既定 `0.0.0.0:8765`） |

---

## 付録B: HashDBM バイナリレイアウト

### B.1 メタデータセクション（先頭128バイト）

| オフセット | 幅 | 内容 |
|---|---|---|
| 0 | 9 | マジック `TkrzwHDB\n` |
| 9 | 1 | 前部サイクリックマジック |
| 10 | 1 | パッケージメジャー版 |
| 11 | 1 | パッケージマイナー版 |
| 12 | 1 | 静的フラグ |
| 13 | 1 | オフセット幅 |
| 14 | 1 | アラインメント指数 |
| 15 | 1 | クローズフラグ |
| 16 | 8 | バケット数（BE） |
| 24 | 8 | レコード数（BE） |
| 32 | 8 | 有効データサイズ（BE） |
| 40 | 8 | ファイルサイズ（BE） |
| 48 | 8 | 最終更新時刻（BE） |
| 56 | 4 | DB種別（BE） |
| 62 | – | 任意データ |
| 127 | 1 | 後部サイクリックマジック |

### B.2 静的フラグ

| ビット | 意味 |
|---|---|
| 0 | 更新モード: in-place |
| 1 | 更新モード: appending |
| 2–3 | CRC: 0=なし, 1=CRC8, 2=CRC16, 3=CRC32 |
| 4–6 | 圧縮: 0=なし, 1=ZLib, 2=ZStd, 3=LZ4, 4=LZMA, 5=RC4, 6=AES |

### B.3 セクション配置

| セクション | 位置 |
|---|---|
| メタデータ | 0 – 127 |
| バケット | 128 – 128 + バケット数 × オフセット幅 |
| フリーブロックプール | 1008 バイト |
| レコードヘッダ | 16 バイト |
| レコード本体 | `record_base = align(128 + バケット数 × オフセット幅 + 1008 + 16, max(4096, 2^align_pow))` |

レコード本体の先頭は 4096 バイト境界に整列される。

### B.4 バケット値

`ReadFixNum(メタデータ + 128 + 索引 × オフセット幅) << align_pow` が示すオフセットから、レコードの単方向リストが始まる。オフセット 0 はリスト終端。

---

*以上*
