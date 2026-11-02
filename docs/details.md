# catfinder 詳細仕様

## 画面仕様

### 画像選択

- 複数ファイルの選択、およびドラッグ＆ドロップに対応する。
- 対応形式: JPEG / PNG / WebP。
- クライアント側の上限: 500 枚、1 枚 15MB。
- 同一ファイル（名前・サイズ・更新時刻が同じ）は重複追加しない。
- 選択済みファイルはサムネイル・ファイル名・サイズとともに表示し、個別に削除できる。

### 判定モデル

- 初期表示時に `GET /api/models` を 1 回呼び、「判定モデル」セレクトボックスで
  使用する vision + Structured Output 対応モデルを選択できる。
- 既定値は `LLM_MODEL`。`LLM_MODEL` が一覧に無い場合は一覧の先頭が既定値に
  なる。`GET /api/models` 自体の取得に失敗した場合（API へのネットワーク断など）は
  セレクトボックスを非表示にし、既定モデルでの送信に任せる。接続先の `/models`
  取得に失敗した場合は、サーバーが既定モデル 1 件を返すため選択肢 1 件で表示される。
- 一覧が空の場合もセレクトボックスを非表示にする。

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
- 「残り時間: 約 X 分（概算）」を表示する。サーバーが `eta_seconds` を返すまでは
  「残り時間: 計算中…」。60 秒未満は「約 X 秒」、60 分未満は分単位、
  それ以上は時間＋分で表示する。ジョブ完了後は表示しない。
- 「別の画像で判定」で初期画面に戻る。サーバー上のジョブは削除されず、
  履歴に残る。

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
- 「実行ログを表示」でジョブの実行ログ（開始・画像ごとの結果・エラー・完了）を
  タイムスタンプ付きで表示する。

### 過去の判定（履歴）

- 初期画面に、SQLite に保存された判定履歴を新しい順に表示する。
- 1 行に日時、使用モデル、「猫あり / 猫なし / エラー」の件数を表示する。
- 履歴を選ぶと既存ジョブの結果画面を開く。ブラウザを閉じた後や API 再起動後も
  同じ内容を表示できる。
- 「別の画像で判定」では履歴を削除しない。履歴の × を押したときだけ削除する。
- 履歴が 0 件のときはセクションを表示しない。

## API 仕様

ベースパスは `/api`。レスポンスは JSON（画像取得を除く）。

### `POST /api/analyses`

`multipart/form-data` で `files` フィールドを複数送信する。
`model` フィールド（任意）で使用する vision + Structured Output 対応モデルを
指定する。未指定・空文字の場合は `GET /api/models` が返す既定値
（`LLM_MODEL`、それが一覧に無ければ一覧の先頭）が使われる。

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
| `400` | 枚数超過 | 一度に送信できる画像は 500 枚までです。 |
| `400` | 画像として不正 | 画像として読み込めないファイルです。 |
| `400` | 空のファイル | 空のファイルです。 |
| `400` | 画素数超過 | 画像の解像度が大きすぎます（上限 4000万画素）。 |
| `400` | 非対応形式 | JPEG / PNG / WebP のみ対応しています。 |
| `400` | 未登録のモデル指定 | 対応していないモデルです。Structured Output に対応した vision モデルを選択してください。 |
| `400` | 推論の強さが不正 | 推論の強さは none / minimal / low / medium / high から選んでください。 |
| `400` | temperature が数値でない | temperature は 0〜2 の数値で指定してください。 |
| `400` | temperature が範囲外 | temperature は 0〜2 の範囲で指定してください。 |
| `400` | max_tokens が不正 | max_tokens は 1〜200000 の整数で指定してください。 |
| `413` | サイズ超過 | 画像が大きすぎます（上限 15MB）。 |
| `503` | `LLM_MODEL` 未設定 | LLM が設定されていません。LLM_MODEL を設定してください。 |
| `503` | 利用可能なモデルが 0 件 | Structured Output に対応した vision モデルがありません。LLM_MODEL を変更してください。 |

検証に失敗した場合、作成途中のジョブディレクトリと SQLite のジョブ行は削除される。

### `GET /api/analyses`

保存済みの判定履歴を新しい順に返す。クエリ `limit`（1〜200、既定 50）と
`offset`（既定 0）でページングできる。

```json
{
  "analyses": [
    {
      "id": "4f1c...",
      "status": "completed",
      "total": 2,
      "cat_count": 1,
      "no_cat_count": 1,
      "error_count": 0,
      "model": "nvidia/nemotron-3.5-super-vl:free",
      "created_at": 1767225600.0,
      "finished_at": 1767225612.3
    }
  ]
}
```

`created_at` / `finished_at` は Unix 時刻（秒）。`finished_at` は完了前なら `null`。

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
  "eta_seconds": 42,
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

`eta_seconds` は判定中の残り時間の超概算（秒）。経過秒 ÷ 完了枚数 × 残り枚数で
計算し、次の場合は `null`。

- ジョブが `running` ではない（`queued` / `completed`）
- 完了枚数が 0
- 全画像が終端状態（`completed` と同義）

同時実行数による短縮は完了枚数の伸びとして反映されるが、画像ごとの処理時間差や
レート制限で大きく変動する。あくまで目安として扱う。

存在しない ID は `404`:

```json
{ "detail": "解析ジョブが見つかりません。" }
```

### `GET /api/analyses/{id}/logs`

実行ログを古い順に返す（最大 1000 件）。

```json
{
  "id": "4f1c...",
  "logs": [
    {
      "id": 1,
      "created_at": 1767225600.1,
      "level": "info",
      "message": "判定を開始しました（2 枚 / モデル: nvidia/nemotron-3.5-super-vl:free）。"
    },
    {
      "id": 2,
      "created_at": 1767225601.2,
      "level": "error",
      "message": "dog.png: 判定に失敗しました: ..."
    }
  ]
}
```

存在しないジョブは `404`。

### `GET /api/analyses/{id}/images/{image_id}`

SQLite に保存された画像（BLOB）を返す。`Content-Type` は JPEG / PNG / WebP の
いずれか。`Cache-Control: private, max-age=300` を付ける。ジョブまたは画像が
存在しない場合は `404`。プロセス再起動後も同じ URL で取得できる。

### `DELETE /api/analyses/{id}`

実行中のタスクをキャンセルし、ジョブ・画像・実行ログを SQLite から削除する。
成功は `204`。存在しない場合は `404`。

### `GET /api/models`

判定に使える vision + Structured Output 対応モデルの一覧。接続先
（`LLM_BASE_URL`）の `GET /models` を呼び出し、次の両方を満たすモデルに絞る。
`LLM_MODEL` が空の場合は接続先を呼び出さず、空の一覧を返す。

- `architecture` で画像入力を明示的に表明している（vision）
- `supported_parameters` に `structured_outputs` を含む（Structured Output）

どちらの区分も取得できない接続先では、その条件を保留して全部を返す。
結果はプロセス内に 600 秒キャッシュし、取得に失敗した場合は 60 秒後に再取得する。

```json
{
  "models": [
    "nvidia/nemotron-3.5-super-vl:free",
    "other-vision-model:free"
  ],
  "default": "nvidia/nemotron-3.5-super-vl:free"
}
```

- 既定値（`LLM_MODEL`）が一覧に含まれる場合は先頭に置き、`default` にも入れる。
- `LLM_MODEL` が一覧に無い場合は、一覧の先頭を `default` として返す
  （ログに警告を出す）。
- 接続先の並びは、無料モデル（`pricing` の prompt / completion がともに 0）を
  先頭に、以降は ID 順に並べ替えてから、既定値を先頭へ移す。
- キャッシュが無い状態で接続先の一覧取得に失敗した場合は、既定値だけを返して
  200 にする（UI が壊れない）。直前のキャッシュがある場合は、それを 60 秒間
  使い続ける。
- 利用可能なモデルが 0 件の場合は `models: []`, `default: ""` を返す。

### `GET /api/health`

```json
{ "status": "ok", "llm_configured": true, "model": "nvidia/nemotron-3.5-super-vl:free" }
```

`LLM_MODEL` が空の場合は `llm_configured: false`, `model: ""` を返す。
API キーなどの秘密情報は返さない。

## LLM 出力仕様

既定では OpenRouter（`https://openrouter.ai/api/v1`）の
`nvidia/nemotron-3.5-super-vl:free` を使う。`LLM_API_KEY` に OpenRouter の
API キーが必要。モデルと接続先は `LLM_MODEL` / `LLM_BASE_URL` で差し替えられる。
判定リクエストに `model` が指定された場合は、そのモデルを使う（未指定なら
`GET /api/models` の既定値）。`reasoning_effort` / `temperature` / `max_tokens`
はリクエストごとに上書きできる。モデルと実効パラメータの組ごとに
`ChatOpenAI` をアナライザー内にキャッシュする。

出力は常に `json_schema` の Structured Output
（`with_structured_output(CatVerdict, method="json_schema", include_raw=True)`）
で受け取る。自由形式の JSON 応答をパースするフォールバックはない。OpenRouter
接続時は `extra_body={"provider": {"require_parameters": True}}` を送り、
要求したパラメータ（`structured_outputs` を含む）に対応する endpoint のみに
ルーティングする。

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

Structured Output に失敗した画像は、その画像だけ `error` になる（フォールバック
なし）。モデル一覧の段階で `structured_outputs` 非対応のモデルを除外しているが、
提供元の endpoint 切り替えなどで実行時に失敗する可能性は残る。

使用量は LangChain の `AIMessage.usage_metadata` から取得する。構造化出力は
`include_raw=True` で raw を取得する。OpenRouter の `usage.cost` は raw の
`response_metadata["token_usage"]["cost"]` から取得する。プロバイダが使用量を
返さない場合は `usage: null` になる。

## 制限値

| 項目 | 既定値 | 環境変数 |
| --- | --- | --- |
| 最大ファイル数 | 500 | `MAX_FILES` |
| 最大ファイルサイズ | 15MB | `MAX_FILE_MB` |
| 最大画素数 | 4000 万 | 固定（`app/images.py`） |
| 最大出力トークン | 200000 | 固定（API 検証） |
| LLM 同時実行数 | 8 | `LLM_CONCURRENCY` |
| LLM タイムアウト | 60 秒 | `LLM_TIMEOUT_SECONDS` |
| LLM リトライ | 2 回 | `LLM_MAX_RETRIES` |
| 履歴の保持 | 自動削除なし | 明示的な `DELETE` のみ |
| SQLite ファイル | `./data/catfinder.db` | `DATABASE_PATH` |
| 作業ディレクトリ | OS の一時ディレクトリ | `UPLOAD_DIR` |

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
- 対象: 正常系、部分失敗、入力検証、上限、404、削除、LLM 未設定、
  モデル指定（既定値・未登録モデルの 400・ジョブへのモデル記録）、
  既定モデルが一覧に無い場合の一覧先頭へのフォールバック・利用可能モデル 0 件の 503、
  API パラメータ（転送・不正値の 400・ジョブへの記録）、使用量の集計と欠損時の `null`。
- 並列実行は `tests/test_api.py` の `ConcurrencyProbe` で、同時に走る判定が
  `llm_concurrency` を超えないことを検証する。
- `tests/test_jobs.py` は `eta_seconds` の計算（完了 0 枚・全完了・開始時刻なしの
  `null` を含む）と、シャットダウン時に実行中の画像が「判定が中断されました。」で
  `error` になることを検証する。
- `tests/test_config.py` は `MAX_FILES` の既定値 500 と上限 500 を検証する。
- `tests/test_storage.py` は SQLite の往復（ジョブ・画像 BLOB・使用量・ログ）、
  履歴の並びと集計、再起動時の中断処理、削除を検証する。
- `tests/test_api.py` は履歴・実行ログの API、履歴削除（`DELETE`）でジョブと
  画像ファイルが消えること、アプリを再起動しても結果・画像が取得できることを
  検証する。
- `tests/test_analyzer.py` は `usage_metadata` からのトークン・コスト抽出、
  常に `json_schema` を使うこと、失敗時にフォールバックしないこと、
  OpenRouter 接続時の `require_parameters` 付与を検証する。
- `tests/test_models.py` は vision + `structured_outputs` の絞り込み、既定モデルの
  解決、fetch 関数を差し替えたモデル一覧の取得・キャッシュ・フォールバックを
  検証する。実プロバイダへの接続は呼ばない。
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
- 判定中の残り時間表示と、`options` を欠いたレスポンスでも結果が表示されることも検証する。
- 501 枚を選択したときに 500 枚で打ち切られ、上限メッセージが出ることも検証する。
- `URL.createObjectURL` は `src/test/setup.ts` でモックする。
- 文言を変更したら `src/App.test.tsx` の日本語セレクタも更新する。
- マウント時は `GET /api/models` と `GET /api/analyses`（履歴）の 2 リクエストが
  走る。`fetch` モックはこの順に消費されるため、アップロード→ポーリングの
  モックはその後に並べる。
- 履歴から過去の判定を開くこと、実行ログを表示することも検証する。

## トラブルシューティング

| 症状 | 確認 |
| --- | --- |
| 判定 API が 503 | `.env` の `LLM_MODEL` が設定されているか、`/api/health` を確認 |
| 判定 API が 400「対応していないモデル」 | UI のモデル選択と `/api/models` の一覧、`LLM_BASE_URL` を確認 |
| モデル選択肢が既定の 1 つしかない | 接続先の `GET /models` が成功しているか。`.server.log` を見る |
| 全画像がエラー | `LLM_BASE_URL`、`LLM_API_KEY`、モデルの vision 対応を確認。`.server.log` を見る |
| OpenRouter で 401 / 403 | `LLM_API_KEY` に有効な OpenRouter のキーが設定されているか確認 |
| 構造化出力で失敗する | `LLM_MODEL` が `/api/models` に出る `structured_outputs` 対応モデルか確認する |
| 詳細設定を指定すると全画像がエラー | モデル／プロバイダがそのパラメータに対応しているか確認。まず「既定」に戻す |
| 使用トークン数が表示されない | プロバイダが `usage` を返しているか確認。返さない場合は `null` になる |
| コストが表示されない | OpenRouter 以外は `usage.cost` を返さないことが多い |
| UI から API に届かない | `scripts/server.sh status` と Vite の proxy 設定を確認 |
| 並列数を上げたら 429 が出る | プロバイダのレート制限。`LLM_CONCURRENCY` を下げる |
| 残り時間が「計算中…」のまま | まだ 1 枚も完了していないか、`eta_seconds` を返す前のレスポンス。1 秒ごとのポーリングで更新される |
| 残り時間が実測と大きくずれる | 超概算のため。画像サイズ・モデル・レート制限で変動する |
| 画像が大きすぎる | `MAX_FILE_MB` を上げるか、クライアント側で縮小する |
| 履歴が表示されない | `DATABASE_PATH` の場所と書き込み権限、`GET /api/analyses` の応答を確認 |
| 過去の画像が表示されない | ジョブが削除されていないか、`GET /api/analyses/{id}/images/{image_id}` を確認 |
| 再起動後に実行中だったジョブがエラー | 仕様。正常終了時は「判定が中断されました。」、異常終了後の再起動時は「サーバー再起動により中断されました。」になる |
| DB が大きくなった | 履歴を DELETE するか、`DATABASE_PATH` のファイルサイズを確認 |

## 既知の制限

- 認証なし。公開環境へそのままデプロイしない。
- 実行中のジョブはプロセス内でも保持する。複数ワーカーで起動すると、実行中の
  削除やキャンセルが別プロセスに届かない可能性がある（`uvicorn --workers 1` 前提）。
  完了済みの履歴は SQLite から読めるため、ワーカーをまたいでも表示できる。
- HEIC / AVIF / GIF 非対応。
- 使用量はプロバイダ依存。判定に成功した画像のみ集計し、失敗したリクエストの
  トークンは含まれない。画像ごとの使用量は API で返さない。
- `reasoning_effort` は `none` / `minimal` / `low` / `medium` / `high` に固定。
  OpenRouter の `xhigh` / `max` や `reasoning.max_tokens` は未対応。
- `eta_seconds` は経過時間と完了枚数だけから求める超概算で、精度は保証しない。
  画像ごとの処理時間差、リトライ、レート制限で大きく変動する。
- 並列数は `LLM_CONCURRENCY`（1〜32、既定 8）。大きくするとプロバイダの
  レート制限（429）に当たりやすくなる。
- 画像は最大 500 枚。合計サイズの上限はなく、500 枚 × 15MB で最大 7.5GB に
  なり得る。画像は SQLite に BLOB で残るため、`DATABASE_PATH` の空き容量に注意する。
- UI は選択画像を一括で描画する。500 枚ではスクロールが重くなる可能性がある。
- UI の上限（500 枚 / 1 枚 15MB）は `frontend/src/App.tsx` の固定値で、
  `MAX_FILES` / `MAX_FILE_MB` を変更してもクライアント側の検証は追従しない。
  サーバー側の設定は API を直接使う場合に反映される。
- 履歴は自動削除しない。ページング API はあるが、UI は直近 50 件のみ表示する。
- 実行ログは古い順に最大 1000 件まで返す。1000 件を超えると新しいログは
  取得できない。
- 個別画像の再判定、キャンセル UI、履歴の検索は未実装。
