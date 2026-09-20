以下に、あなたの思想（サイドカー型LLM拡張・エージェント前提）と、実際のTkrzw / tkrzw-dictの構造（高性能KVSベース）を踏まえた**実装可能レベルの仕様書**を提示します。

※Tkrzwは高速なKVS（Key-Value Store）として設計されており、Pythonバインディングでも同様に高速検索・永続化が可能です ([DBMx][1])

---

# Tkrzw-Dict Python版 for AI Agents

## ― AIエージェント向け語彙サイドカー拡張モジュール仕様書 ―

---

## 1. 概要

本仕様は、既存のTkrzw-Dictをベースとし、

> **AIエージェントがオンデマンドで呼び出す語彙補助サイドカー**

として再設計した
**「Tkrzw-Dict Python版 for AI Agents」**の仕様を定義する。

---

## 2. 設計目的

### 2.1 背景

小型LLM（30B以下）では以下の問題が発生する：

* 高難度英単語の誤訳
* 専門用語の不安定な解釈
* 文脈依存の意味分岐に弱い

---

### 2.2 解決方針

```text
LLM（推論）
  ↓
語彙サイドカー（本モジュール）
  ↓
候補提示（ヒント）
```

👉 **翻訳ではなく「意味候補提示」**

---

## 3. アーキテクチャ位置付け

```text
LLM Agent
  ↓
Tool Orchestrator
  ↓
Sidecar Modules
  ├ RAG（知識）
  ├ Tools（機能）
  ├ State（状態）
  ├ Memory（記憶）
  └ Vocabulary Support（本モジュール）
```

---

## 4. システム構成

### 4.1 コンポーネント

```text
tkrzw_dict_agent/
  ├ core/
  │   ├ db.py
  │   ├ query.py
  │   ├ scorer.py
  │   └ normalizer.py
  ├ agent_api/
  │   ├ tool_schema.json
  │   ├ handler.py
  │   └ router.py
  ├ pipelines/
  │   ├ extract_terms.py
  │   ├ enrich.py
  ├ cli/
  │   └ main.py
```

---

## 5. データ構造

### 5.1 基本レコード（KVS）

```json
{
  "word": "tightening",
  "pos": "noun",
  "meanings": [
    {
      "ja": "金融引き締め",
      "domain": "economics",
      "score": 0.92
    },
    {
      "ja": "強化",
      "domain": "general",
      "score": 0.65
    }
  ],
  "synonyms": ["contraction", "restriction"],
  "related": ["interest rate", "inflation"],
  "frequency": 0.00012
}
```

---

### 5.2 拡張フィールド

* `domain`（分野タグ）
* `score`（文脈適合度）
* `frequency`（出現頻度）

---

## 6. コア機能

---

### 6.1 語彙検索

```python
lookup(word: str) -> Dict
```

* 完全一致検索
* O(1) KVSアクセス

---

### 6.2 コンテキスト適合

```python
resolve(word: str, context: str) -> List[Meaning]
```

* 文脈ベースで意味を再ランキング

---

### 6.3 バッチ処理

```python
enrich(text: str) -> Dict
```

出力：

```json
{
  "terms": [
    {
      "word": "tightening",
      "candidates": ["金融引き締め", "強化"]
    }
  ]
}
```

---

### 6.4 重要語抽出

```python
extract_terms(text: str) -> List[str]
```

* TF-IDF
* 固有表現優先

---

## 7. スコアリングアルゴリズム

```text
score =
  base_frequency
+ domain_match
+ context_similarity
```

---

## 8. AIエージェント連携

---

### 8.1 Tool定義

```json
{
  "name": "vocabulary_support",
  "description": "Provide Japanese hints for difficult English words",
  "parameters": {
    "text": "string"
  }
}
```

---

### 8.2 呼び出しフロー

```text
1. LLMが理解困難を検出
2. vocabulary_support呼び出し
3. 候補取得
4. 再生成
```

---

### 8.3 自動発火条件

```text
・英語率 > 30%
・未知語率 > 閾値
・専門分野検出
```

---

## 9. 出力フォーマット（重要）

```text
[参考語彙]
tightening:
- 金融引き締め（経済）
- 強化（一般）
```

👉
**LLMに選択させる設計**

---

## 10. CLI仕様

```bash
dict lookup tightening
dict enrich "Fed tightening policy"
dict extract "long text"
```

---

## 11. API仕様（FastAPI）

```text
POST /lookup
POST /enrich
POST /extract
```

---

## 12. パフォーマンス要件

| 項目         | 目標     |
| ---------- | ------ |
| lookup     | < 1ms  |
| enrich（1文） | < 10ms |
| DBサイズ      | 数GB対応  |

---

## 13. ストレージ設計

* Tkrzw HashDBM使用
* インデックス：単語キー
* オプション：TreeDBM（prefix検索）

👉 高速KVSにより低レイテンシ実現 ([DBMx][1])

---

## 14. 拡張性

---

### 14.1 多言語対応

* 英→日
* 英→多言語

---

### 14.2 分野特化辞書

* 医療
* 金融
* IT

---

### 14.3 Wikipedia連携

```text
Wikipedia → 単語抽出 → enrich
```

---

## 15. 運用方針

* 辞書は定期更新
* スコアはログから再学習
* 不要語はブラックリスト化

---

## 16. 本設計の本質

本モジュールは単なる辞書ではなく：

> **LLMの語彙理解能力を外部化した「能力サイドカー」**

である。

---

## 17. 結論

Tkrzw-Dict Python版 for AI Agentsは、

* 小型LLMの翻訳精度向上
* 専門用語処理の安定化
* エージェントの自律性向上

を実現する、

> **低コスト・高効果のサイドカー拡張コンポーネント**

である。

---


[1]: https://dbmx.net/tkrzw/api-python/?utm_source=chatgpt.com "Module and Classes — Python binding of Tkrzw"
