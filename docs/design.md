# catfinder 設計

## 目的

複数の画像ファイルの中から、猫が写っている画像を見つける。
判定は vision 対応 LLM に委ね、利用者は Web UI で進捗と結果を確認する。

### 非目標

- 画像の長期保存・履歴管理・ユーザーアカウント
- 猫の個体識別、品種判定、物体検出の座標出力
- 大量画像（数千枚）を継続処理する分散キュー

## 全体構成

```text
Browser
  │  multipart (複数画像)
  ▼
React + Vite (frontend/)
  │  /api は Vite proxy で転送
  ▼
FastAPI (app/main.py)
  ├─ 画像検証・一時保存 (app/images.py)
  ├─ インメモリ Job (app/jobs.py)
  ├─ モデル一覧の取得とキャッシュ (app/models.py)
  └─ LangChain ChatOpenAI (app/analyzer.py)
          │
          ▼
  OpenAI 互換 API（vision 対応モデル）
```

- フロントエンドと API は別プロセス。開発時は `scripts/server.sh` と
  `scripts/ui.sh` で起動する。
- Vite の `server.proxy` が `/api` を `http://127.0.0.1:8000` に転送するため、
  開発時に CORS 設定は不要。本番も同一オリジンで配信する想定。
- DB は使わない。ジョブと結果はプロセス内メモリ、画像は一時ディレクトリ。

## リクエストの流れ

1. UI がマウント時に `GET /api/models` を 1 回呼び、選択可能な vision モデルと
   既定値を取得する。
2. UI が選択画像を `FormData` の `files` フィールドで、選択モデルを `model`
   フィールドで、任意の API パラメータを `reasoning_effort` / `temperature` /
   `max_tokens` フィールドで `POST /api/analyses` に送る。
3. API が枚数・サイズ・実データ形式・パラメータを検証し、ジョブディレクトリへ
   UUID 名で保存する。
4. ジョブをメモリに登録し、`asyncio.create_task` で判定を開始して 202 を返す。
5. 各画像はセマフォで同時実行数を制限しつつ、ジョブのモデルとパラメータに
   対応する LLM クライアントで 1 枚ずつ問い合わせる。
6. 判定結果と一緒に、プロバイダが返したトークン使用量を画像ごとに記録する。
7. UI は 1 秒間隔で `GET /api/analyses/{id}` をポーリングする。
8. 全画像が終端状態になるとジョブは `completed` になり、ポーリングが止まる。
9. TTL を過ぎたジョブはバックグラウンドのクリーンアップで削除される。

## ジョブと画像の状態

ジョブ:

```text
queued ──> running ──> completed
```

画像:

```text
pending ──> running ──> cat
                    ├─> no_cat
                    └─> error
```

- 画像 1 枚の失敗は `error` として記録し、ジョブ全体は継続する。
- `completed` は「全画像が終端状態になった」ことを意味し、成功だけを意味しない。
- `error_count` と `completed`（終端状態の数）を分けて返す。

## モジュールの責務

| ファイル | 責務 |
| --- | --- |
| `app/main.py` | `create_app()`、ルーティング、リクエスト検証（API パラメータ含む）、レスポンス変換 |
| `app/config.py` | `pydantic-settings` による環境変数読み込み |
| `app/models.py` | 接続先 `/models` からの vision モデル一覧取得、キャッシュ、許可判定 |
| `app/images.py` | 実データ検証、表示用ファイル名の無害化、一時保存 |
| `app/analyzer.py` | LangChain メッセージ構築、構造化出力、JSON フォールバック、モデル・パラメータ別クライアントのキャッシュ、usage 抽出 |
| `app/jobs.py` | ジョブ登録、並列実行、集計（usage 含む）、削除、TTL クリーンアップ |
| `app/schemas.py` | API と LLM 出力の Pydantic モデル（`ChatOptions` / `TokenUsage` 含む） |
| `frontend/src/api.ts` | HTTP クライアント、API パラメータの送信、エラー整形 |
| `frontend/src/useAnalysis.ts` | アップロード（API パラメータ含む）、ポーリング、エラー状態 |
| `frontend/src/App.tsx` | 画面状態、ファイル選択、モデル・API パラメータ選択、使用量表示、フィルタ |

`create_app(settings, analyzer, catalog)` はテスト用の注入点である。
本番では `create_analyzer(settings)` が `MultiModelCatAnalyzer` を組み立てる。
既定の接続先は OpenRouter で、モデルは `nvidia/nemotron-3.5-super-vl:free`。
`LLM_BASE_URL` / `LLM_MODEL` を上書きすれば他の OpenAI 互換 API に差し替えられる。
`LLM_MODEL` が空の場合はアナライザーが `None` になり、判定 API は 503 を返す。

## LLM 連携

```python
ChatOpenAI(
    model=model,
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key,
    timeout=settings.llm_timeout_seconds,
    max_retries=settings.llm_max_retries,
    temperature=options.temperature if options.temperature is not None else 0,
    reasoning_effort=options.reasoning_effort,  # 指定時のみ
    max_tokens=options.max_tokens,              # 指定時のみ
)
```

- 既定値は OpenRouter（`https://openrouter.ai/api/v1`）の
  `nvidia/nemotron-3.5-super-vl:free`。`LLM_API_KEY` に OpenRouter のキーが必要。
- モデルを差し替える場合は `LLM_MODEL` を変更する。接続先も変える場合は
  `LLM_BASE_URL` / `LLM_API_KEY` を併せて上書きする。
- `LLM_MODEL` は「モデル選択が無い／失敗したときの既定値」であり、
  UI の「判定モデル」ではリクエストごとにモデルを上書きできる。
- UI の「詳細設定」で `reasoning_effort` / `temperature` / `max_tokens` を
  リクエストごとに指定できる。未指定時は temperature 0、それ以外は
  プロバイダ既定を使う。
- `ChatOpenAI` はモデルと実効パラメータ（推論の強さ・temperature・max_tokens）
  の組ごとに 1 個生成し、`MultiModelCatAnalyzer` がプロセス内にキャッシュする。
  判定構造（プロンプト・構造化出力）はモデル間で共有する。
- 選択肢の並び順は、無料モデルを先頭・以降 ID 順に並べ替える（既定値は常に最初）。
  vision 判定は `architecture` が存在しない接続先では保留（全部返す）、存在する
  場合は画像入力を明示的に表明するモデルのみ残す。
- 画像は LangChain の標準 content block
  `{"type": "image", "base64": ..., "mime_type": ...}` で渡す。
- 出力は `CatVerdict`（`has_cat`, `confidence`, `reason`）に固定する。
- `LLM_STRUCTURED_OUTPUT=auto`（既定）は `json_schema` を試し、例外時に
  JSON 応答をパースする方式へフォールバックする。OpenAI 互換サーバーの
  構造化出力対応差を吸収するため。
- `reason` は日本語で返すようプロンプトで指示する。
- `confidence` は 0–1 を基本とするが、0–100 や `"87%"` で返る場合も
  正規化して受け入れる。
- 構造化出力は `include_raw=True` で呼び、raw の `AIMessage.usage_metadata` から
  input / output / total / reasoning トークンを取得する。JSON 応答モードも同じ。
- `LLM_STRUCTURED_OUTPUT=auto` で JSON フォールバックした場合は、構造化出力の
  試行とフォールバックの usage を合算する。失敗した試行でもトークンを消費するため。
- OpenRouter の `usage.cost` は raw の
  `response_metadata["token_usage"]["cost"]` から取得し、ジョブ合計に加算する。
- usage は判定に成功した画像のみ集計する。プロバイダが返さない場合は `null` にし、
  UI では表示しない。
- 1 枚ごとに 1 リクエスト。複数画像の一括送信はモデル互換性が下がるため行わない。

## 画像の検証と保存

- 拡張子や `Content-Type` は信用せず、Pillow でデコードして形式を判定する。
- 対応形式は JPEG / PNG / WebP。
- 上限は 1 枚 15MB（`MAX_FILE_MB`）、4000 万画素、1 リクエスト 50 枚（`MAX_FILES`）。
- 保存名は UUID。元のファイル名は表示専用で、パスには使わない。
- `UPLOAD_DIR` 未指定時は `tempfile.mkdtemp(prefix="catfinder-")` を使う。
- 結果表示のため、画像はジョブ削除または TTL まで保持する。

## 並列性とクリーンアップ

- `asyncio.Semaphore(LLM_CONCURRENCY)` で LLM 呼び出しを制限する。
- ジョブ内の画像は `asyncio.gather` で並列処理する。
- タイムアウトとリトライは LangChain / OpenAI クライアント側の設定に委ねる。
- クリーンアップはアプリ起動時の lifespan で開始し、TTL の半分（5–60 秒）ごとに
  期限切れジョブを削除する。
- アプリ終了時は実行中タスクをキャンセルし、保存ディレクトリを削除する。

## フロントエンドの状態

- 選択ファイルは `SelectedImage`（`File` + object URL）として保持する。
- object URL は追加時に生成し、削除・リセット・アンマウント時に明示的に revoke する。
- モデル一覧はマウント時の 1 リクエストで取得する。失敗時はセレクトボックスを
  隠して既定モデルに任せ、UI エラーにはしない。
- 選択モデルは送信時に `POST /api/analyses` の `model` フィールドへ渡す。
  結果画面はジョブが実際に使った `model` を表示する。
- API パラメータは `ChatOptionInput`（空文字 = 未指定）で保持し、送信時だけ
  `reasoning_effort` / `temperature` / `max_tokens` を `FormData` に追加する。
  「詳細設定」は折りたたみで、既定は閉じた状態にする。
- 結果画面はジョブが実際に使ったパラメータと、合計トークン数（取得できた場合は
  コスト）を表示する。画像ごとの使用量は API では返さず、ジョブ合計のみ扱う。
- アップロード後はサーバーが返す画像 URL を表示する。
- ポーリングは `AbortController` と `setTimeout` で管理し、ジョブ変更・
  アンマウント時に停止する。
- 実行中は再送信と別ジョブへの遷移を無効化する。
- ステータスに対応する CSS クラスは `status-cat` / `status-no_cat` /
  `status-error` / `status-running` / `status-pending`。

## セキュリティと運用上の割り切り

- 認証・認可はない。ローカルまたは信頼済みネットワークでの利用を前提とする。
- アップロード画像は外部 LLM API に送信される。機密画像の利用可否は利用者が判断する。
- エラーメッセージには LLM 例外の内容を含む（最大 300 文字）。ローカル用途の
  デバッグ性を優先した割り切りで、公開サービスにする場合はサニタイズが必要。
- 結果はプロセス再起動で消える。永続化が必要になったら SQLite などを追加する。

## 拡張の候補

- 失敗画像だけの再判定
- SSE / WebSocket による進捗配信
- 判定履歴の永続化と検索
- 画像のクライアント側リサイズによる転送量削減
- 複数モデルの比較・設定画面
