# LexGap（小型LLM日本語語彙欠落検証システム）実装計画書_20260921

| 項目   | 内容                                                      |
| ---- | ------------------------------------------------------- |
| 版    | v1.0                                                    |
| 作成日  | 2026-09-21                                              |
| 基盤文書 | LexGap企画設計書 v0.1、Tkrzw-Dict Python版 for AI Agents 仕様書   |
| 対象読者 | 実装者（個人開発、単一運用者）                                         |
| 前提環境 | 自宅LAN内Ubuntu 24.04、RAM 96GB、ストレージ4TB、Python 3.12、標準venv |

---

## 目次

1. 本計画書の位置づけと全体方針
2. 前提条件と開発環境セットアップ
3. リポジトリ構成
4. コンポーネント別実装計画
   - 4.1 tkrzw-dict-agent 拡張（事前作業）
   - 4.2 SQLite スキーマ実装
   - 4.3 Sampler
   - 4.4 ProbeRunner
   - 4.5 Judge
   - 4.6 FeatureBuilder
   - 4.7 Predictor
   - 4.8 Exporter / gate.json
   - 4.9 InterventionEvaluator
   - 4.10 Reporter
   - 4.11 CLI統合
5. マイルストーン別作業分解（M0〜M5）
6. 未決事項の解消計画（Q-01〜Q-09）
7. テスト計画
8. 運用・保守計画
9. コスト・スケジュール見積もり
10. リスク対応の実装落とし込み
11. 完了定義（Definition of Done）

---

## 1. 本計画書の位置づけと全体方針

### 1.1 位置づけ

企画設計書v0.1は「何を作るか・なぜ作るか」を定義した。本計画書は「**どう作るか**」を、コード構成・処理フロー・タスク分解・検収基準のレベルまで具体化する。実装中に判断に迷った場合の優先順位は以下の通り。

1. **再現性**（温度0・シード固定・版管理の徹底）
2. **中断再開可能性**（キャッシュ・冪等性）
3. **依存最小**（設計方針1.5に準拠）
4. 実行速度（最後に最適化する。早すぎる最適化を禁止）

### 1.2 実装上の重要原則

| 原則 | 具体策 |
|---|---|
| 決定的部分と非決定的部分の分離 | Sampler/Judgeは決定的実装。モデル呼び出し（Probe/審判）のみ非決定的とし、結果は全てキャッシュ |
| 設定はコードでなくデータで | モデル構成・プロンプト・サンプル仕様はTOML/JSONで外部化 |
| 失敗してもDBを壊さない | SQLiteのトランザクション単位を1プローブ単位に。部分書き込み禁止 |
| 概算値は実測で更新 | コスト概算（§6.2の13〜33時間等）はM1完了時に実測値へ置き換える |

---

## 2. 前提条件と開発環境セットアップ

### 2.1 必要パッケージ（`requirements.txt`）

```
regex>=2024.0
httpx>=0.27          # OpenAI互換APIクライアント（同期/非同期両対応）
numpy>=2.0
scikit-learn>=1.5
tkrzw>=0.1           # tkrzw-dict-agentのDB直接参照用
# 任意（Q-08の結果次第）
sentencepiece>=0.2   # HFトークナイザ代替が必要な場合
transformers>=4.45   # 同上、必要時のみインストール
```

注：FastAPI・pytestは開発用として`requirements-dev.txt`に分離する。

### 2.2 ディレクトリ初期化

```bash
mkdir -p ~/lexgap && cd ~/lexgap
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
git init
```

### 2.3 対象LLMランナーの疎通確認（M0の一部）

LM Studio / llama.cpp serverいずれかを `http://127.0.0.1:8080/v1` で起動し、以下で疎通確認する。

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"test","messages":[{"role":"user","content":"hello"}],"temperature":0}'
```

疎通確認の結果を `configs/runners.toml` に記録する（ホスト・ポート・コンテキスト長上限・対数確率取得可否）。

---

## 3. リポジトリ構成

```
lexgap/
├── lexgap/                  # 本体パッケージ
│   ├── __init__.py
│   ├── config.py            # TOMLローダ、パス解決
│   ├── db.py                # SQLite接続・スキーマ初期化・マイグレーション
│   ├── dict_adapter.py      # tkrzw-dict-agent 読み出しアダプタ（§4.1）
│   ├── sampler.py           # §4.3
│   ├── prompter.py          # プロンプト雛形のレンダリング・版管理
│   ├── runner.py            # ProbeRunner §4.4
│   ├── judge.py             # §4.5
│   ├── normalizer.py        # 日本語正規化（Judge内部で使用）
│   ├── features.py          # §4.6
│   ├── predictor.py         # §4.7
│   ├── exporter.py          # §4.8
│   ├── intervention.py      # §4.9
│   ├── reporter.py          # §4.10
│   └── audit.py             # LLM審判・監査サンプル抽出
├── configs/
│   ├── runners.toml         # LLMランナー接続設定
│   ├── models.toml          # モデル構成（§6.1に対応）
│   └── prompts/             # p1_v1.txt, p2_v1.txt ...
├── specs/
│   └── s1.toml              # サンプリング仕様（版管理対象）
├── data/
│   ├── lexgap.db            # SQLite（gitignore）
│   └── corpus/              # 介入評価用コーパス（gitignore）
├── tests/
├── scripts/
│   └── m0_envcheck.py       # 環境確認スクリプト
├── lexgap_cli.py            # CLIエントリポイント §4.11
└── README.md
```

---

## 4. コンポーネント別実装計画

### 4.1 tkrzw-dict-agent 拡張（事前作業）

設計書§8.3の要望を先回りして実装する。LexGap本体は本アダプタ経由でのみ辞書に触れる。

**タスク一覧：**

| # | タスク | 成果物 | 完了条件 |
|---|---|---|---|
| T-A1 | 頻度フィールド調査（Q-01） | 調査メモ | 各レコードから頻度値を取得可能、欠損率を記録 |
| T-A2 | `dict_adapter.py` 実装 | モジュール | 下記APIが動作 |
| T-A3 | 語義・例文対応調査（Q-02） | 調査メモ | 例文がどの語義に対応するか判定可否を結論 |

**アダプタAPI：**

```python
class DictAdapter:
    def iter_headwords(self) -> Iterator[dict]:
        """見出し語レコードを全件走査。word/pos/freq/domain/senses/examplesを返す"""
    def lookup(self, word: str) -> dict | None:
        """完全一致検索"""
    def gloss_set(self, word: str) -> set[str]:
        """該当語の全語義の日本語訳語集合（Judge用）"""
```

- tkrzwのHashDBMを直接開く。`enrich`のHTTP APIは使わずライブラリ直接呼び出し（設計書§8.3の方針）。
- 見出し語総数約50万件の走査時間をM0で実測・記録する。

### 4.2 SQLite スキーマ実装

設計書§7のDDLに加え、以下を追加する。

```sql
CREATE TABLE features (
  set_id TEXT, headword TEXT, model_id TEXT,
  feature_json TEXT, created_at TEXT,
  PRIMARY KEY (set_id, headword, model_id));
CREATE TABLE predictions (
  set_id TEXT, model_id TEXT, headword TEXT,
  prob_missing REAL, predictor_ver TEXT, PRIMARY KEY (set_id, model_id, headword));
CREATE TABLE audit_tasks (
  id INTEGER PRIMARY KEY, probe_id INTEGER, auditor TEXT,
  status TEXT, result TEXT, created_at TEXT);
CREATE TABLE intervention_runs (
  id INTEGER PRIMARY KEY, condition TEXT, corpus_id TEXT,
  model_id TEXT, sentence_id TEXT, raw_output TEXT,
  tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
```

実装ノート：
- `db.py` に `init_db()`（冪等）と `get_conn()` を実装。WALモード有効化（`PRAGMA journal_mode=WAL`）で読み書き競合を減らす。
- `probes` の UNIQUE制約 `(set_id, headword, model_id, task, prompt_ver)` がキャッシュの要。挿入前SELECTで既存なら実行スキップ。

### 4.3 Sampler

**処理フロー：**

1. `specs/s1.toml` を読み込む（`seed`, `n_quantiles=20`, `per_quantile=100`, `aux_multi_sense=200`, `aux_domains=各200` を定義）
2. `DictAdapter.iter_headwords()` で全件走査し、英字のみ語をフィルタ
3. 対数頻度で20分位に分割
4. 各分位からシード固定で復元なし抽出（`random.Random(seed).sample`）
5. 補助層（語義数5以上・分野別）を同様に抽出。主層との重複は許容するがフラグを付記
6. `sample_sets` と `samples` にトランザクション投入

**検収基準：** 同一シードで再実行し、抽出語集合が完全一致すること（AC4の一部）。

**TOML例：**

```toml
[set]
id = "S1"
seed = 42
[main]
n_quantiles = 20
per_quantile = 100
filter_regex = "^[A-Za-z-]+$"
[aux]
multi_sense_min = 5
multi_sense_n = 200
domain_n = 200
```

### 4.4 ProbeRunner

**基本設計：**

- OpenAI互換 `/v1/chat/completions` に対する非同期クライアント（httpx + asyncio）
- 並列度は `runners.toml` の `max_concurrency` で制御（ローカルランナーでは初期値2。VRAM逼迫を避ける）
- 1プローブ = 1トランザクション。失敗時は指数バックオフで最大3回リトライ、超限は `probes` に `raw_output=NULL, parsed_json='{"error":...}'` として記録（再試行対象として識別可能にする）

**プロンプト版管理：**

- `configs/prompts/p1_v1.txt` のようにファイル名で版管理
- `prompter.py` が `{sentence}` `{word}` 等をレンダリング
- 使用した版を `probes.prompt_ver` に記録

**P2の選択肢生成（Q-09の実装、初期方針）：**

1. 正解肢：対象語の最有力語義の訳語（文脈とする例文に対応する語義。対応不明の場合はスコア上位語義）
2. 誤答肢：同品詞・対数頻度差±0.5以内の別見出し語の訳語から2つ + 対象語の別語義から1つ
3. 選択肢の並びは seed 固定でシャッフル（位置バイアス対策）
4. 誤答肢が正解語義と重複する場合は再抽出

**任意計測：** ランナーが `logprobs` を返せる場合は平均対数確率を保存。温度0.7×3回の自己整合性は `--sc` フラグで有効化（デフォルトOFF、コスト3倍のため）。

### 4.5 Judge

**二段構成：**

```
raw_output
  → normalize()        # NFKC、全角半角統一、長音/中黒正規化、かな統一
  → match()            # 許容訳語集合との照合
  → K / P / U / X ラベル付与
  → (U/Xの一部) audit queue へ
```

**照合規則の優先順位：**

| 順 | 条件 | ラベル |
|---|---|---|
| 1 | 正規化後に訳語集合と完全一致 | K |
| 2 | 訴語集合のいずれかを部分文字列として含む（許容長の上下限あり） | P |
| 3 | 上位語・一般化訳（例：正解「金融引き締め」→出力「引き締め」） | P |
| 4 | 不一致だが辞書外の別訳の可能性がある | X |
| 5 | 明確な不一致 | U |

3と4の切り分けが容易でないため、初期実装では **3と4は区別せず全て監査キューへ**送り、監査結果で閾値を事後決定する（これによりQ-09相当の判断をデータ駆動化する）。

**監査フロー：**

- 各頻度層からランダムに初期200件を `audit_tasks` に投入
- 審判LLM（強いモデル、Q-05で確定）に「正解訳語集合 + 文脈 + 出力」を渡し二値判定
- 審判プロンプトも版管理（`judge_audit_v1.txt`）
- 審判出力もキャッシュ（同一入力に二度課金しない）

**検収基準：** 監査後に Judge の適合率を算出し、AC3（90%以上）を判定できること。

### 4.6 FeatureBuilder

設計書§5.5の表を実装対象として落とす。

| 特徴量 | 実装ソース | 備考 |
|---|---|---|
| log_freq | DictAdapter | Q-01で確定 |
| pos, n_senses | DictAdapter | |
| log n_examples | DictAdapter | |
| sense_dominance | lookupのスコア比（top1/top2） | |
| context_sense_gap | resolve の top1−top2 スコア差 | 例文あり時のみ |
| domain_onehot(6) | DictAdapter | |
| word_len, is_inflected, is_compound | 見出し語から算出 | 屈折判定は簡易規則（-ed/-ing/-s除去で見出し語一致） |
| n_subwords | Q-08の結果次第 | HFトークナイザ or ランナーAPI |
| ja_gloss_rarity | 保留（要検討項目） | v1では除外可 |
| avg_logprob, self_consistency | probes から | 任意フラグ |

出力は `features` テーブルにJSONで格納。特徴量定義の変更はコード版で管理し、学習時の特徴量リストを `gate.json` にも記録する。

### 4.7 Predictor

**実装手順：**

1. 学習データ構築：`labels = (judgments.label == 'K')`、Pは除外、Xは除外
2. グループ分割：語幹（小文字化＋末尾-s除去の簡易ステマ）でGroupKFold(5)
3. ベースライン B0：`log_freq` のみのロジスティック回帰
4. B1：B0 + `n_senses`
5. Full：全特徴量のロジスティック回帰（StandardScaler適用）
6. 評価：AUROC、PR-AUC、ECE（10ビン）、頻度層別AUROC、ブートストラップ1,000回で95%CI
7. 較正：必要なら `CalibratedClassifierCV`（isotonic）で較正
8. θ決定：期待損失最小化。損失関数 `L(θ) = miss_cost × FN(θ) + token_cost × ヒント付与数(θ)` をグリッドサーチ。初期値 miss_cost:token_cost = 10:1 とし、感度分析で 5:1〜20:1 も報告

**検収基準：** AC1（B0に対するAUROC改善がCIで有意）を `lexgap fit` の出力として自動判定。

### 4.8 Exporter / gate.json

- `lexgap export --model <id>` で設計書§8.2の形式を出力
- ロジスティック回帰の係数・切片・特徴量名・スケーラー統計量を全てJSON内に含め、**受け取り側（tkrzw-dict-agent）がnumpy無しの純Pythonで推論できる**ことを保証する（推論は `sigmoid(w·x + b)` のみ）
- tkrzw-dict-agent 側には `enrich --gate gate.json` オプションを追加実装する：
  - 欠落確率 ≥ θ の語のみ候補を出力
  - 既知判定の語は `terms` から除外
  - 語義単位ゲーティング（C4用）：`context_sense_gap ≥ φ` の場合は語が既知でも候補を付与（φも gate.json に含める）

### 4.9 InterventionEvaluator

**条件実装：**

| 条件 | enrich呼び出し方法 |
|---|---|
| C0 | enrichスキップ（ヒントなしで翻訳のみ） |
| C1 | enrich全件 |
| C2 | B0のしきい値でフィルタ |
| C3 | gate.jsonでフィルタ |
| C4 | gate.json + 語義単位ゲーティング |
| C5 | C0実行後、訳出力文中の対象語訳が候補集合外の語のみ再プロンプト |

**評価コーパス：**
- (a) 辞書例文由来：S1から層化抽出（初期200文）
- (b) 実運用文書：RSS記事を条件（英字率、文長）でフィルタし手作業で50〜100文選定
- (c) FLORES-200英日：ライセンス確認後に採否（Q-07と連動）

**指標出力：**
1. 稀少語訳語正解率：翻訳出力をJudgeと同一正規化で評価（辞書の対象語訳語が出力に含まれるか）
2. chrF：参照訳がある場合（sacrebleuは依存追加となるため、仮導入可。追加する場合はrequirements-devに）
3. LLMペアワイズ審判：位置入れ替え2回で極性を検出、不一致は除外
4. トークン数・遅延：`intervention_runs` から集計
5. 誤誘導率：C0で正解・Cnで不正解となった語の割合

**検収基準：** AC2（非劣性マージン2ポイント＋トークン50%削減）を自動判定出力。

### 4.10 Reporter

- `lexgap report` で以下を生成（Matplotlibなしで実装するため、出力は Markdown 表 + CSV。グラフ化は後工程・任意）
  - 頻度層別 K/P/U/X/X 率（H1の正答率曲線の表形式）
  - 推定器評価サマリ（AUROC等・CI）
  - 量子化間比較表（H4）：同一モデルのQ4/Q8の層別正答率差と McNemar検定
  - P1−P2差分（H3）：語ごとの「選択できるが生成できない」フラグの集計
  - 介入評価パレート：品質×トークンの表

### 4.11 CLI統合

設計書§8.1のコマンド体系を `argparse` で実装（click等の追加依存は避ける）。全コマンド共通で `--db`（デフォルト `data/lexgap.db`）と `--verbose` を受け付ける。進捗は10%ごとにINFOログ出力。全コマンドは中断後に同一コマンドを再実行すれば続きから動くことを確認する統合テストを用意（§7）。

---

## 5. マイルストーン別作業分解（M0〜M5）

### M0：環境確認（規模S、想定2〜3日）

| # | タスク | 成果物 |
|---|---|---|
| M0-1 | venv・依存インストール | 動作する環境 |
| M0-2 | tkrzw-dict DB疎通・全件走査時間実測 | 計測メモ |
| M0-3 | 頻度フィールド確認（Q-01） | 調査メモ→設計書更新 |
| M0-4 | 例文-語義対応確認（Q-02） | 調査メモ→設計書更新 |
| M0-5 | ランナー疎通（logprobs可否含む） | `runners.toml` |
| M0-6 | スキーマ初期化 `init_db()` | DBファイル |

### M1：S1・1モデルで欠落曲線（規模M、想定1〜2週）

| # | タスク | 依存 |
|---|---|---|
| M1-1 | DictAdapter実装・単体テスト | M0 |
| M1-2 | Sampler実装・S1生成・再現性検証 | M1-1 |
| M1-3 | prompter + P1プロンプトv1確定 | — |
| M1-4 | ProbeRunner実装（キャッシュ・リトライ・並列） | M0-5 |
| M1-5 | P1実行（S1×1モデル＝2,000件）。所要時間実測→総コスト見積もり更新 | M1-4 |
| M1-6 | normalizer + Judge実装 | M1-3 |
| M1-7 | 監査200件（LLM審判）・適合率算出→AC3判定 | M1-6 |
| M1-8 | 頻度層別正答率レポート（H1の一次結果） | M1-7 |

**M1完了条件：** S1・1モデルで頻度層別曲線が出力され、監査でJudge精度が確認できる。

### M2：P2・特徴量・推定器（規模M、想定1〜2週)

| # | タスク |
|---|---|
| M2-1 | P2選択肢生成（Q-09初期方針）＋プロンプトv1 |
| M2-2 | P2実行（S1×同一モデル） |
| M2-3 | FeatureBuilder実装（subwords等、Q-08解決後に確定） |
| M2-4 | B0/B1/Fullの学習・評価・較正 |
| M2-5 | θ決定（感度分析含む）→ AC1判定 |

### M3：量子化・モデル間比較（規模M、想定1〜2週）

| # | タスク |
|---|---|
| M3-1 | モデル系統A（BF16相当/Q8/Q4）の構成登録（sha256記録） |
| M3-2 | S1プローブを全系統で実行（M1の実測値で時間見積もり再計算） |
| M3-3 | H4：量子化間比較（McNemar、層別差） |
| M3-4 | 系統B・Cで実行しH3補強（C系はQ-04次第。枝刈りモデルが用意困難なら日本語能力の低い公開モデルで代替） |
| M3-5 | H3：P1−P2差分のモデル間比較レポート |

### M4：介入評価（規模L、想定2〜3週）

| # | タスク |
|---|---|
| M4-1 | 評価コーパス構築（a）必須・(b)(c)はライセンス確認後 |
| M4-2 | InterventionEvaluator実装（C0〜C4、C5任意） |
| M4-3 | 全条件実行 |
| M4-4 | ヒューマン/LLM審判による文品質評価 |
| M4-5 | 非劣性検定＋トークン削減率 → AC2判定 |
| M4-6 | 誤誘導率分析・失敗事例集 |

### M5：Exporterとenrich連携（規模S、想定2〜3日）

| # | タスク |
|---|---|
| M5-1 | exporter実装（gate.json出力） |
| M5-2 | tkrzw-dict-agent側 `enrich --gate` 実装（純Python推論） |
| M5-3 | エンドツーエンド動作確認・README更新 |

---

## 6. 未決事項の解消計画

| Q | 解消タイミング | 解消方法 | 未解消時のフォールバック |
|---|---|---|---|
| Q-01 頻度フィールド | M0 | DB実調査 | 外部公開頻度表（wordfreq等）で代替、注記 |
| Q-02 例文-語義対応 | M0 | 実データ調査 | C4（語義単位）を任意項目に降格、スコア上位語義で代用 |
| Q-03 モデル確定 | M1開始時 | 96GB環境で動作確認できる最新30B級を選定 | まず1モデルでM1を完了、追加は並行輸入 |
| Q-04 日本語劣化モデル | M3 | 枝刈り or 日本語の弱い公開モデルで代替 | H3は「判定不能」を許容（AC5で認められている） |
| Q-05 審判LLM | M1（監査前） | 利用規約確認・小規模テスト | 完全人手監査（件数半減） |
| Q-06 分野タグ粒度 | M2 | 特徴量重要度で寄与確認 | 寄与小なら特徴量から除外 |
| Q-07 データライセンス | M4前 | 各データ源の規約確認 | (c)不参加・結果の公開範囲限定 |
| Q-08 サブワード数 | M2 | ランナーのAPI→不可ならHFトークナイザ | 当該特徴量を除外 |
| Q-09 P2誤答肢 | M2-1 | §4.4の初期方針で実装、監査で検証 | 誤答肢生成ルールの反復改善（v2プロンプトとして版管理） |

---

## 7. テスト計画

### 7.1 単体テスト（pytest）

| 対象 | 主なケース |
|---|---|
| normalizer | NFKC/長音/かな変換の網羅ケース（約30件の手作りペア） |
| judge | K/P/U/X各パターン、境界ケース |
| sampler | 再現性（同一シード一致）、分位境界、除外正規表現 |
| dict_adapter | 存在語・非存在語・多義語 |
| runner | モックAPIでのリトライ・既存レコードスキップ・エラー記録 |
| predictor | 小データでの学習・θ探索・gate.json往復 |
| exporter | gate.json → 純Python推論の一致 |

### 7.2 統合テスト

1. **冪等性テスト**：`probe` を途中でCtrl+C → 再実行で重複レコードがなく完走
2. **E2Eスモーク**：10語ミニセットで sample→probe→judge→fit→export を通す（CI的に都度使用）
3. **AC4テスト**：同一シード・同一設定で samples/labels が完全一致

### 7.3 監査ベース検証

M1の200件監査をJudgeの「テストセット」として保存し、Judge改修時は必ず再測定して適合率の回帰を防ぐ。

---

## 8. 運用・保守計画

- **DBバックアップ**：介入評価完了ごとに `lexgap.db` を日付付きコピー（4TB相当の余裕があるためローテーションは月次で十分）
- **プロンプト・モデル構成の変更**：必ずファイル名/IDをインクリメントし、過去版と混在させない
- **ログ**：`logs/YYYYMMDD_<cmd>.log` に集約。エラーは `probes.parsed_json.error` にも残しDB横断で集計可能に
- **LLMランナー**：必要時のみ起動。バッチ実行後は停止スクリプトを回す
- **外部LLMコスト管理**：監査呼び出しに月次件数上限を設定（初期500件/月）、超過時はコマンド拒否

---

## 9. コスト・スケジュール見積もり

### 9.1 開発工数（個人開発・実働ベース概算）

| フェーズ | 実装工数 | 実験待ち時間 |
|---|---|---|
| M0 | 2〜3日 | 半日 |
| M1 | 5〜7日 | プローブ数時間〜1日 |
| M2 | 5〜7日 | 1日 |
| M3 | 3〜4日 | 2〜4日（全構成プローブ） |
| M4 | 7〜10日 | 2〜3日 |
| M5 | 2〜3日 | 半日 |
| **合計** | **約4〜6週間** | 約1週間 |

### 9.2 計算コスト（M1実測で更新）

- M1だけなら 2,000語×1タスク×1モデル＝2,000リクエスト。2〜5秒/件なら直列1〜3時間
- 全体（設計書§6.2）は最大24,000件規模。並列度2なら7〜17時間見込み

### 9.3 審判コスト

- 監査：初期200件＋各マイルストーン追加分＝概算500〜1,000件
- M4のペアワイズ審判：条件間×文数×2回（位置入れ替え）＝1,000件前後
- 合計で外部API利用は2,000件規模に留める設計

---

## 10. リスク対応の実装落とし込み

| リスク（設計書§10） | 実装上の対応 |
|---|---|
| 辞書網羅性不足による偽陰性 | Xラベル分離＋監査キューを実装（§4.5）。U≠欠落確定として分析 |
| モデル学習データに辞書が混入 | 報告書に「既知の過大評価バイアス」節を必須章立て |
| プローブ形式依存 | P1/P2/単語単体の3形式を比較可能なDB設計（task列） |
| H4がノイズに埋没 | S2拡張を判断する分岐点をM3-3に設定（効果量<2ptかつCI幅大→S2実施） |
| 審判LLMのコスト・バイアス | キャッシュ・上限・位置入れ替え検証（§4.9） |
| 実装者の途中断念 | 各Mを独立した完結単位にし、M1時点でも「頻度別欠落曲線」という成果物が残る設計 |

---

## 11. 完了定義（Definition of Done）

本プロジェクトは以下を全て満たした時点で完了とする。

1. **AC1〜AC5の全てに対し、判定結果（達成/未達/判定不能）と根拠データが `report` の出力として存在する**
2. M1〜M5の完了条件を全て満たす（M6は任意）
3. `gate.json` を使った `enrich --gate` が少なくとも1モデル構成で動作実証されている
4. 全コードがリポジトリにコミットされ、E2Eスモークテストが通る
5. H1〜H5それぞれについて、支持/不支持/判定不能のいずれかが実データで裏付けられ、最終報告書（Markdown、設計書の続編として）に記述されている
6. 語彙層で確立した「診断→推定→ゲーティング→介入評価」の手順が、事実層・手順層へ転用可能な形で一般化され文書化されている（設計書§13の対応表の更新）

---

## 付録：実装開始時チェックリスト

```text
[ ] venv構築・依存インストール済み
[ ] tkrzw-dict DBへのパス確認・走査時間実測済み
[ ] runners.toml 疎通確認済み（logprobs可否記録）
[ ] init_db() 実行済み
[ ] scripts/m0_envcheck.py が全項目OK
[ ] specs/s1.toml がコミット済み
[ ] M1用モデル1系統がローカルで起動可能
```
