# catfinder 詳細仕様

## 画面仕様

### 画像選択

- 複数ファイルの選択、およびドラッグ＆ドロップに対応する。
- 対応形式: JPEG / PNG / WebP。
- クライアント側の上限: 50 枚、1 枚 15MB。
- 同一ファイル（名前・サイズ・更新時刻が同じ）は重複追加しない。
- 選択済みファイルはサムネイル・ファイル名・サイズとともに表示し、個別に削除できる。

### 判定中

- 送信後は進捗バーと「n / m 枚を判定しました。」を表示する。
- 初回レスポンス前は不定形の進捗表示になる。
- 「別の画像で判定」でサーバー上のジョブを削除して初期画面に戻る。

### 結果表示

- 画像ごとにサムネイル、ファイル名、ステータス、確信度、理由を表示する。
- ステータスは「猫あり」「猫なし」「エラー」「判定中」「待機中」。
- 集計は「猫あり」「猫なし」「エラー」の件数。
- フィルタは「すべて」「猫あり」「猫なし」「エラー」。
- エラー画像はエラー内容を表示する。

## API 仕様

ベースパスは `/api`。レスポンスは JSON（画像取得を除く）。

### `POST /api/analyses`

`multipart/form-data` で `files` フィールドを複数送信する。

```bash
curl -X POST http://127.0.0.1:8000/api/analyses \
  -F "files=@cat.jpg" \
  -F "files=@dog.png"
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

### `GET /api/health`

```json
{ "status": "ok", "llm_configured": true, "model": "your-vision-model" }
```

API キーなどの秘密情報は返さない。

## LLM 出力仕様

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

## 制限値

| 項目 | 既定値 | 環境変数 |
| --- | --- | --- |
| 最大ファイル数 | 50 | `MAX_FILES` |
| 最大ファイルサイズ | 15MB | `MAX_FILE_MB` |
| 最大画素数 | 4000 万 | 固定（`app/images.py`） |
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
- 対象: 正常系、部分失敗、入力検証、上限、404、削除、LLM 未設定、JSON フォールバック。
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
- `URL.createObjectURL` は `src/test/setup.ts` でモックする。
- 文言を変更したら `src/App.test.tsx` の日本語セレクタも更新する。

## トラブルシューティング

| 症状 | 確認 |
| --- | --- |
| 判定 API が 503 | `.env` の `LLM_MODEL` が設定されているか、`/api/health` を確認 |
| 全画像がエラー | `LLM_BASE_URL`、API キー、モデルの vision 対応を確認。`.server.log` を見る |
| 構造化出力で失敗する | `LLM_STRUCTURED_OUTPUT=json` を試す |
| UI から API に届かない | `scripts/server.sh status` と Vite の proxy 設定を確認 |
| 画像が大きすぎる | `MAX_FILE_MB` を上げるか、クライアント側で縮小する |
| 結果が消えた | 再起動または TTL 超過。履歴は保存されない |

## 既知の制限

- 認証なし。公開環境へそのままデプロイしない。
- ジョブは単一プロセス内のみ。複数ワーカーで起動するとポーリング先と
  ジョブ保持プロセスが一致しない可能性がある（`uvicorn --workers 1` 前提）。
- HEIC / AVIF / GIF 非対応。
- 結果の永続化、個別画像の再判定、キャンセル UI は未実装。
