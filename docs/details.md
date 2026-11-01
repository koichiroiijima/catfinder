# catfinder 詳細仕様

## 画面仕様

### 画像選択

- 複数ファイルの選択、およびドラッグ＆ドロップに対応する。
- 対応形式: JPEG / PNG / WebP。
- クライアント側の上限: 50 枚、1 枚 15MB。
- 同一ファイル（名前・サイズ・更新時刻が同じ）は重複追加しない。
- 選択済みファイルはサムネイル・ファイル名・サイズとともに表示し、個別に削除できる。

### 判定モデル

- 初期表示時に `GET /api/models` を 1 回呼び、「判定モデル」セレクトボックスで
  使用する vision モデルを選択できる。
- 既定値は `LLM_MODEL`。選択肢が取得できなかった場合（接続先が `/models` を
  持たない、ネットワーク断など）はセレクトボックス自体を非表示にし、
  既定モデルでの送信に任せる。

### 詳細設定

折りたたみの「詳細設定」で、判定リクエストの API パラメータを指定できる。
既定は閉じた状態で、未指定の項目は送信しない。

| 項目 | 入力 | 未指定時 |
| --- | --- | --- |
| 推論の強さ | 既定 / なし / 最小 / 低 / 中 / 高 | プロバイダ既定 |
| Temperature | 0〜2 の数値 | 0 |
| 最大出力トークン | 1〜200000 の整数 | プロバイダ既定 |

モデルやプロバイダが対応していないパラメータを指定すると、その画像の判定は
エラーになる。エラー内容は結果カードに表示される。

### 判定中

- 送信後は進捗バーと「n / m 枚を判定しました。」を表示する。
- 初回レスポンス前は不定形の進捗表示になる。
- 「別の画像で判定」でサーバー上のジョブを削除して初期画面に戻る。

### 結果表示

- 画像ごとにサムネイル、ファイル名、ステータス、確信度、理由を表示する。
- ステータスは「猫あり」「猫なし」「エラー」「判定中」「待機中」。
- 集計は「猫あり」「猫なし」「エラー」の件数。
- 使用したモデルを「判定モデル: <モデル名>」として表示する。
- 指定した API パラメータを「設定: 推論: 高 / temperature: 0.2 / 最大出力: 1,000」
  のように表示する。すべて未指定の場合は表示しない。
- 判定に成功した画像の合計トークン数を「使用トークン: 12,345」として表示する。
  プロバイダがコストを返した場合は「（コスト: $0.001200）」も併記する。
  使用量が返らない場合は表示しない。
- フィルタは「すべて」「猫あり」「猫なし」「エラー」。
- エラー画像はエラー内容を表示する。

## API 仕様

ベースパスは `/api`。レスポンスは JSON（画像取得を除く）。

### `POST /api/analyses`

`multipart/form-data` で `files` フィールドを複数送信する。
`model` フィールド（任意）で使用する vision モデルを指定する。
未指定・空文字の場合は `LLM_MODEL` が使われる。

任意の API パラメータ:

| フィールド | 型 | 値 | 未指定時 |
| --- | --- | --- | --- |
| `reasoning_effort` | 文字列 | `none` / `minimal` / `low` / `medium` / `high` | プロバイダ既定 |
| `temperature` | 文字列（数値） | 0〜2 | 0 |
| `max_tokens` | 文字列（整数） | 1〜200000 | プロバイダ既定 |

空文字は未指定として扱う。

```bash
curl -X POST http://127.0.0.1:8000/api/analyses \
  -F "files=@cat.jpg" \
  -F "files=@dog.png" \
  -F "model=nvidia/nemotron-3.5-super-vl:free" \
  -F "reasoning_effort=high" \
  -F "temperature=0.2" \
  -F "max_tokens=1000"
```

成功: `202 Accepted`

```json
{
  "id": "4f1c...",
  "status": "queued",
  "total": 2
}
```

エラー:

| ステータス | 条件 | `detail` の例 |
| --- | --- | --- |
| `400` | ファイル 0 枚 | 画像を1枚以上選択してください。 |
| `400` | 枚数超過 | 一度に送信できる画像は 50 枚までです。 |
| `400` | 画像として不正 | 画像として読み込めないファイルです。 |
| `400` | 非対応形式 | JPEG / PNG / WebP のみ対応しています。 |
| `400` | 未登録のモデル指定 | 対応していないモデルです。選択肢から選んでください。 |
| `400` | 推論の強さが不正 | 推論の強さは none / minimal / low / medium / high から選んでください。 |
| `400` | temperature が不正 | temperature は 0〜2 の範囲で指定してください。 |
| `400` | max_tokens が不正 | max_tokens は 1〜200000 の整数で指定してください。 |
| `413` | サイズ超過 | 画像が大きすぎます（上限 15MB）。 |
| `503` | `LLM_MODEL` 未設定 | LLM が設定されていません。LLM_MODEL を設定してください。 |

検証に失敗した場合、作成途中のジョブディレクトリは削除される。

### `GET /api/analyses/{id}`

```json
{
  "id": "4f1c...",
  "status": "running",
  "total": 2,
  "completed": 1,
  "cat_count": 1,
  "no_cat_count": 0,
  "error_count": 0,
  "model": "nvidia/nemotron-3.5-super-vl:free",
  "options": {
    "reasoning_effort": "high",
    "temperature": 0.2,
    "max_tokens": 1000
  },
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 56,
    "total_tokens": 1290,
    "reasoning_tokens": 12,
    "cost": 0.00042
  },
  "images": [
    {
      "id": "9a2b...",
      "filename": "cat.jpg",
      "status": "cat",
      "has_cat": true,
      "confidence": 0.93,
      "reason": "茶色の猫が中央に写っています。",
      "error": null,
      "url": "/api/analyses/4f1c.../images/9a2b..."
    },
    {
      "id": "1c3d...",
      "filename": "dog.png",
      "status": "running",
      "has_cat": null,
      "confidence": null,
      "reason": null,
      "error": null,
      "url": "/api/analyses/4f1c.../images/1c3d..."
    }
  ]
}
```

`status` は `queued` / `running` / `completed`。
画像の `status` は `pending` / `running` / `cat` / `no_cat` / `error`。

`options` はジョブ作成時に指定された API パラメータで、未指定の項目は `null`。
`usage` は判定に成功した画像の合計で、プロバイダが使用量を返さない場合は `null`。
`cost` は OpenRouter の `usage.cost` が取得できた場合のみ入る。画像ごとの
使用量は返さない。

存在しない ID は `404`:

```json
{ "detail": "解析ジョブが見つかりません。" }
```

### `GET /api/analyses/{id}/images/{image_id}`

保存済み画像を返す。`Content-Type` は JPEG / PNG / WebP のいずれか。
ジョブまたは画像が存在しない場合は `404`。

### `DELETE /api/analyses/{id}`

実行中のタスクをキャンセルし、ジョブと画像を削除する。成功は `204`。
存在しない場合は `404`。

### `GET /api/models`

判定に使える vision モデルの一覧。接続先（`LLM_BASE_URL`）の `GET /models`
を呼び出し、`architecture` で画像入力を明示的に表明しているモデルに絞って返す
（vision の区分が取れない接続先では全部を返す）。結果はプロセス内に
600 秒キャッシュし、取得に失敗した場合は 60 秒後に再取得する。

```json
{
  "models": [
    "nvidia/nemotron-3.5-super-vl:free",
    "other-vision-model:free"
  ],
  "default": "nvidia/nemotron-3.5-super-vl:free"
}
```

- 既定値（`LLM_MODEL`）は一覧の先頭に含めて返す。
- 接続先の並びは、無料モデル（`pricing` の prompt / completion がともに 0）を
  先頭に、以降は ID 順に並べ替えて返す（既定値が常に最初に来る点は変わらない）。
- 接続先の一覧取得に失敗した場合も、既定値だけを返して 200 にする（UI が壊れない）。

### `GET /api/health`

```json
{ "status": "ok", "llm_configured": true, "model": "nvidia/nemotron-3.5-super-vl:free" }
```

API キーなどの秘密情報は返さない。

## LLM 出力仕様

既定では OpenRouter（`https://openrouter.ai/api/v1`）の
`nvidia/nemotron-3.5-super-vl:free` を使う。`LLM_API_KEY` に OpenRouter の
API キーが必要。モデルと接続先は `LLM_MODEL` / `LLM_BASE_URL` で差し替えられる。
判定リクエストに `model` が指定された場合は、そのモデルを使う（未指定なら
`LLM_MODEL`）。`reasoning_effort` / `temperature` / `max_tokens` はリクエスト
ごとに上書きできる。モデルと実効パラメータの組ごとに `ChatOpenAI` を
アナライザー内にキャッシュする。

モデルには次の構造を要求する。

```json
{
  "has_cat": true,
  "confidence": 0.87,
  "reason": "日本語の短い理由"
}
```

- `has_cat`: 画像内に猫が 1 匹以上写っていれば `true`。
- `confidence`: 0–1。0–100 や `"87%"` も正規化して受け入れる。
- `reason`: 日本語。500 文字を超える場合は切り詰める。
- 猫以外のおもちゃ・置物・イラスト・ロゴ・文字は猫として数えないよう指示する。

`LLM_STRUCTURED_OUTPUT` の値:

| 値 | 動作 |
| --- | --- |
| `auto` | まず `json_schema`。失敗時に JSON パースへフォールバック |
| `json_schema` | 構造化出力のみ。失敗時は画像をエラーにする |
| `function_calling` | tool calling による構造化出力のみ |
| `json` | 通常応答を JSON としてパース |

使用量は LangChain の `AIMessage.usage_metadata` から取得する。構造化出力は
`include_raw=True` で raw を取得し、`auto` の JSON フォールバック時は構造化出力と
フォールバックの両方の試行分を合算する。OpenRouter の `usage.cost` は raw の
`response_metadata["token_usage"]["cost"]` から取得する。プロバイダが使用量を
返さない場合は `usage: null` になる。

## 制限値

| 項目 | 既定値 | 環境変数 |
| --- | --- | --- |
| 最大ファイル数 | 50 | `MAX_FILES` |
| 最大ファイルサイズ | 15MB | `MAX_FILE_MB` |
| 最大画素数 | 4000 万 | 固定（`app/images.py`） |
| 最大出力トークン | 200000 | 固定（API 検証） |
| LLM 同時実行数 | 4 | `LLM_CONCURRENCY` |
| LLM タイムアウト | 60 秒 | `LLM_TIMEOUT_SECONDS` |
| LLM リトライ | 2 回 | `LLM_MAX_RETRIES` |
| 結果保持時間 | 60 分 | `ANALYSIS_TTL_MINUTES` |

## テスト

### バックエンド

```bash
uv run pytest
uv run pytest tests/test_api.py::test_mixed_results_and_image_serving
uv run pytest tests/test_analyzer.py -q
uv run ruff check .
```

- `tests/conftest.py` の `FakeAnalyzer` を注入するため、通常のテストは実 LLM を呼ばない。
- API テストは `TestClient` の lifespan を起動し、バックグラウンド判定をポーリングする。
- 対象: 正常系、部分失敗、入力検証、上限、404、削除、LLM 未設定、JSON フォールバック、
  モデル指定（既定値・未登録モデルの 400・ジョブへのモデル記録）、
  API パラメータ（転送・不正値の 400・ジョブへの記録）、使用量の集計と欠損時の `null`。
- `tests/test_analyzer.py` は `usage_metadata` からのトークン・コスト抽出と、
  フォールバック時の使用量合算も検証する。
- `tests/test_models.py` は fetch 関数を差し替えてモデル一覧の取得・キャッシュ・
  フォールバックを検証する。実プロバイダへの接続は呼ばない。
- 実モデルの確認は明示的に有効化する:

  ```bash
  RUN_LLM_INTEGRATION=1 \
  LLM_INTEGRATION_IMAGE=/path/to/cat.jpg \
  uv run pytest tests/test_integration.py -q
  ```

  `.env` の LLM 設定が必要。未設定時はテストがスキップされる。

### フロントエンド

```bash
cd frontend
npm test
npm run lint
npm run build
```

- `fetch` をモックし、アップロード→ポーリング→結果表示を検証する。
- 「詳細設定」の値が `FormData` に載ること、使用トークン数・コストが表示されることも検証する。
- `URL.createObjectURL` は `src/test/setup.ts` でモックする。
- 文言を変更したら `src/App.test.tsx` の日本語セレクタも更新する。
- `fetch` モックの先頭は `GET /api/models`（マウント時のモデル一覧取得）が
  消費するため、アップロード→ポーリングのモックはその後に並べる。

## トラブルシューティング

| 症状 | 確認 |
| --- | --- |
| 判定 API が 503 | `.env` の `LLM_MODEL` が設定されているか、`/api/health` を確認 |
| 判定 API が 400「対応していないモデル」 | UI のモデル選択と `/api/models` の一覧、`LLM_BASE_URL` を確認 |
| モデル選択肢が既定の 1 つしかない | 接続先の `GET /models` が成功しているか。`.server.log` を見る |
| 全画像がエラー | `LLM_BASE_URL`、`LLM_API_KEY`、モデルの vision 対応を確認。`.server.log` を見る |
| OpenRouter で 401 / 403 | `LLM_API_KEY` に有効な OpenRouter のキーが設定されているか確認 |
| 構造化出力で失敗する | `LLM_STRUCTURED_OUTPUT=json` を試す |
| 詳細設定を指定すると全画像がエラー | モデル／プロバイダがそのパラメータに対応しているか確認。まず「既定」に戻す |
| 使用トークン数が表示されない | プロバイダが `usage` を返しているか確認。返さない場合は `null` になる |
| コストが表示されない | OpenRouter 以外は `usage.cost` を返さないことが多い |
| UI から API に届かない | `scripts/server.sh status` と Vite の proxy 設定を確認 |
| 画像が大きすぎる | `MAX_FILE_MB` を上げるか、クライアント側で縮小する |
| 結果が消えた | 再起動または TTL 超過。履歴は保存されない |

## 既知の制限

- 認証なし。公開環境へそのままデプロイしない。
- ジョブは単一プロセス内のみ。複数ワーカーで起動するとポーリング先と
  ジョブ保持プロセスが一致しない可能性がある（`uvicorn --workers 1` 前提）。
- HEIC / AVIF / GIF 非対応。
- 使用量はプロバイダ依存。判定に成功した画像のみ集計し、失敗したリクエストの
  トークンは含まれない。画像ごとの使用量は API で返さない。
- `reasoning_effort` は `none` / `minimal` / `low` / `medium` / `high` に固定。
  OpenRouter の `xhigh` / `max` や `reasoning.max_tokens` は未対応。
- 結果の永続化、個別画像の再判定、キャンセル UI は未実装。
