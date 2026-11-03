# catfinder 設計

## 目的

複数の画像ファイルの中から、猫が写っている画像を見つける。
判定は vision 対応 LLM に委ね、利用者は Web UI で進捗と結果を確認する。

### 非目標

- ユーザーアカウント、認証、複数ユーザーごとの履歴分離
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
  ├─ 画像検証・作業ディレクトリへの保存 (app/images.py)
  ├─ ジョブ実行と実行ログ (app/jobs.py)
  ├─ SQLite 永続化 (app/storage.py)
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
- ジョブ、判定結果、画像（BLOB）、実行ログは SQLite に保存する。実行中の
  ジョブはプロセス内にも置くが、履歴はプロセス再起動後も SQLite から読み出せる。

## リクエストの流れ

1. UI がマウント時に `GET /api/models` で選択可能な
   vision + Structured Output 対応モデルと既定値を取得し、`GET /api/analyses` で
   保存済みの履歴を取得する。
2. UI が選択画像を `FormData` の `files` フィールドで、選択モデルを `model`
   フィールドで、任意の API パラメータを `reasoning_effort` / `temperature` /
   `max_tokens` フィールドで `POST /api/analyses` に送る。
3. API が枚数・サイズ・実データ形式・パラメータを検証し、ジョブディレクトリへ
   UUID 名で保存する。各画像は SQLite に BLOB として登録する。
4. ジョブをメモリに登録し、`asyncio.create_task` で判定を開始して 202 を返す。
5. 各画像はセマフォで同時実行数（既定 8）を制限しつつ、ジョブのモデルと
   パラメータに対応する LLM クライアントで 1 枚ずつ並列に問い合わせる。
6. 判定結果と一緒に、プロバイダが返したトークン使用量を画像ごとに記録し、
   実行ログを SQLite に追記する。
7. UI は 1 秒間隔で `GET /api/analyses/{id}` をポーリングし、進捗と
   残り時間の概算を受け取る。
8. 全画像が終端状態になるとジョブは `completed` になり、ポーリングが止まる。
   作業ディレクトリは削除し、以降は SQLite の画像を配信する。
9. 履歴は自動削除しない。UI の履歴から `DELETE /api/analyses/{id}` を呼ぶと
   ジョブ・画像・ログがまとめて削除される。

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
- プロセス再起動時に `queued` / `running` のジョブが残っていた場合は、
  ジョブを `completed`、未完了の画像を `error`（「サーバー再起動により
  中断されました。」）に更新し、実行ログにも記録する。

## 残り時間の概算

- `AnalysisJob.started_at` を判定開始時に記録する。
- `eta_seconds = 経過秒 / 完了枚数 × 残り枚数` を超概算として返す。
  同時実行数による短縮は、完了枚数の伸びとして自然に反映される。
- 完了 0 枚、または全画像が終端状態のときは `null`。
- 画像ごとの処理時間差やレート制限の影響で変動する。UI では「概算」と明示し、
  計算できない間は「計算中…」と表示する。

## モジュールの責務

| ファイル | 責務 |
| --- | --- |
| `app/main.py` | `create_app()`、ルーティング、リクエスト検証（API パラメータ含む）、レスポンス変換 |
| `app/config.py` | `pydantic-settings` による環境変数読み込み |
| `app/models.py` | 接続先 `/models` からの vision + Structured Output 対応モデル一覧取得、パラメータ対応状況の抽出、キャッシュ、許可判定 |
| `app/images.py` | 実データ検証、表示用ファイル名の無害化、作業ディレクトリへの保存 |
| `app/analyzer.py` | LangChain メッセージ構築、`json_schema` 構造化出力、モデル・パラメータ別クライアントの LRU キャッシュ（上限 32）と解放、usage 抽出 |
| `app/jobs.py` | ジョブ登録、並列実行、集計（usage・残り時間含む）、実行ログ、削除 |
| `app/storage.py` | SQLite のスキーマ、ジョブ・画像 BLOB・実行ログの読み書き、DB ファイルの権限、再起動時の中断処理 |
| `app/domain.py` | ジョブと画像の内部データ構造 |
| `app/schemas.py` | API と LLM 出力の Pydantic モデル（`ChatOptions` / `TokenUsage` / 履歴 / ログ含む） |
| `frontend/src/api.ts` | HTTP クライアント、API パラメータの送信、履歴・実行ログ取得、エラー整形 |
| `frontend/src/useAnalysis.ts` | アップロード（API パラメータ含む）、ポーリング、既存ジョブのオープン、エラー状態 |
| `frontend/src/App.tsx` | 画面状態、ファイル選択、モデル・API パラメータ選択、履歴・実行ログ表示、フィルタ |

`create_app(settings, analyzer, catalog)` はテスト用の注入点である。
本番では `create_analyzer(settings)` が `MultiModelCatAnalyzer` を組み立てる。
既定の接続先は OpenRouter で、モデルは `nvidia/nemotron-3.5-super-vl:free`。
`LLM_BASE_URL` / `LLM_MODEL` を上書きすれば他の OpenAI 互換 API に差し替えられる。
`LLM_MODEL` が空の場合はアナライザーが `None` になり、判定 API は 503 を返す。

## LLM 連携

```python
kwargs = dict(
    model=model,
    base_url=settings.llm_base_url,
    api_key=settings.llm_api_key,
    timeout=settings.llm_timeout_seconds,
    max_retries=settings.llm_max_retries,
    reasoning_effort=options.reasoning_effort,  # 指定時のみ
    max_tokens=options.max_tokens,              # 指定時のみ
    http_client=http_client,                    # この ChatOpenAI 専用
    http_async_client=http_async_client,
    http_socket_options=(),                     # 既定 transport 注入を止める
)
if supports_temperature:
    kwargs["temperature"] = options.temperature if options.temperature is not None else 0.0
ChatOpenAI(**kwargs)
```

- temperature は `GET /api/models` の `capabilities` を見て、対応モデルにだけ送る。
  非対応モデルへ送ると OpenRouter の `require_parameters` と組み合わさって
  「要求パラメータを満たす endpoint が無い」404 になるため。
  未対応モデルでは `temperature` を kwargs に含めず、プロバイダ既定に任せる。

- 既定値は OpenRouter（`https://openrouter.ai/api/v1`）の
  `nvidia/nemotron-3.5-super-vl:free`。`LLM_API_KEY` に OpenRouter のキーが必要。
- モデルを差し替える場合は `LLM_MODEL` を変更する。接続先も変える場合は
  `LLM_BASE_URL` / `LLM_API_KEY` を併せて上書きする。
- `LLM_MODEL` は「モデル選択が無い／失敗したときの既定値」であり、
  UI の「判定モデル」ではリクエストごとにモデルを上書きできる。
  `LLM_MODEL` が Structured Output 対応モデルの一覧に無い場合は、一覧の先頭を
  既定値として使う。
- UI の「詳細設定」で `reasoning_effort` / `temperature` / `max_tokens` を
  リクエストごとに指定できる。temperature はモデルが対応している場合のみ
  未指定時に 0 を使い、非対応モデルでは送らない。それ以外はプロバイダ既定を使う。
- `GET /api/models` はモデル ID に加えて `capabilities`
  （`supports_temperature` / `supports_reasoning_effort` / `supported_efforts`）を返す。
  `supported_parameters` を返さない接続先では capability を省略し、未知として
  従来どおりパラメータを送る。UI は `supports_temperature: false` のモデルで
  Temperature 入力を無効化し、入力済みの値をクリアする。
- `ChatOpenAI` はモデルと実効パラメータ（推論の強さ・temperature・max_tokens）
  の組ごとに 1 個生成し、`MultiModelCatAnalyzer` が LRU
  （上限 `MAX_CACHED_ANALYZERS = 32`）でプロセス内にキャッシュする。
  あふれたクライアントは、そのクライアントを使っているリクエストが終わってから
  閉じる。アプリ終了時はキャッシュを空にし、未使用のクライアントを解放する
  （使用中のものはリクエスト完了時に解放する）。判定構造（プロンプト・
  構造化出力）はモデル間で共有する。
- `ChatOpenAI` ごとに専用の `httpx.Client` / `httpx.AsyncClient` を渡す。
  langchain-openai は既定の HTTP クライアントをプロセス内で共有するため、
  共有クライアントを閉じると別モデルの判定まで壊れるのを避ける。
  キャッシュから追い出すときとシャットダウン時に、専用クライアントだけを閉じる。
- 選択肢の並び順は、無料モデルを先頭・以降 ID 順に並べ替える（既定値が一覧に
  含まれる場合は常に最初）。vision 判定は `architecture` が存在しない接続先では
  保留（全部返す）、存在する場合は画像入力を明示的に表明するモデルのみ残す。
- Structured Output 判定は `supported_parameters` の `structured_outputs` を
  見る。メタデータが無い接続先では保留（全部返す）。OpenRouter 接続時は
  `extra_body={"provider": {"require_parameters": True}}` を送り、要求した
  パラメータ（Structured Output を含む）に対応する endpoint のみにルーティングする。
- 画像は LangChain の標準 content block
  `{"type": "image", "base64": ..., "mime_type": ...}` で渡す。
- 出力は `CatVerdict`（`has_cat`, `confidence`, `reason`）に固定し、常に
  `with_structured_output(..., method="json_schema")` で受け取る。自由形式の
  JSON 応答をパースするフォールバックは持たない。非対応のモデル・endpoint では
  その画像がエラーになる（モデル一覧で事前に除外する）。
- `reason` は日本語で返すようプロンプトで指示する。
- `confidence` は 0–1 を基本とするが、0–100 や `"87%"` で返る場合も
  正規化して受け入れる。
- 構造化出力は `include_raw=True` で呼び、raw の `AIMessage.usage_metadata` から
  input / output / total / reasoning トークンを取得する。
- OpenRouter の `usage.cost` は raw の
  `response_metadata["token_usage"]["cost"]` から取得し、ジョブ合計に加算する。
- usage は判定に成功した画像のみ集計する。プロバイダが返さない場合は `null` にし、
  UI では表示しない。
- 1 枚ごとに 1 リクエスト。複数画像の一括送信はモデル互換性が下がるため行わない。

## 画像の検証と保存

- 拡張子や `Content-Type` は信用せず、Pillow でデコードして形式を判定する。
- 対応形式は JPEG / PNG / WebP。
- 上限は 1 枚 15MB（`MAX_FILE_MB`）、4000 万画素、1 リクエスト 500 枚（`MAX_FILES`）。
- 保存名は UUID。元のファイル名は表示専用で、パスには使わない。
- `UPLOAD_DIR` 未指定時は `tempfile.mkdtemp(prefix="catfinder-")` を作業ディレクトリに使う。
- 画像は受け付けた時点で SQLite の `job_images.image`（BLOB）に保存する。
  作業ディレクトリは判定中だけ使い、ジョブ完了時・削除時に削除する。
- 結果表示は SQLite の BLOB を `Response` で返す。プロセス再起動後も同じ URL で取得できる。

## 並列性と永続化

- `asyncio.Semaphore(LLM_CONCURRENCY)` で LLM 呼び出しを制限する（既定 8）。
- ジョブ内の画像は `asyncio.gather` で並列処理する。全件を一斉送信せず、
  セマフォで実際の HTTP リクエスト数を抑える。
- タイムアウトとリトライは LangChain / OpenAI クライアント側の設定に委ねる。
- SQLite への読み書きは `asyncio.to_thread` で行い、イベントループを止めない。
  読み書きは 1 本の `asyncio.Lock` で直列化する。
- SQLite の DB 本体とサイドカー（`-wal` / `-shm` / `-journal`）は `0600`、
  新しく作る保存先ディレクトリは `0700` にする。既存ディレクトリの権限は
  自動変更しない。
- `scripts/server.sh` は `umask 077` で起動し、新規作成される DB・ログ・PID
  ファイルを所有者のみに制限する。
- 履歴の自動削除はしない。`DELETE /api/analyses/{id}` で明示的に削除する。
- アプリ終了時は実行中タスクをキャンセルし、未完了の画像を `error`
  （「判定が中断されました。」）、ジョブを `completed` に更新する。履歴（SQLite）は
  保持し、作業ディレクトリだけを削除する。異常終了などで更新できなかった
  `queued` / `running` のジョブは、次回起動時に `recover_interrupted()` が
  「サーバー再起動により中断されました。」で完了扱いにする。

## フロントエンドの状態

- 選択ファイルは `SelectedImage`（`File` + object URL）として保持する。
- object URL は追加時に生成し、削除・リセット・アンマウント時に明示的に revoke する。
- モデル一覧はマウント時の 1 リクエストで取得する。`GET /api/models` 自体が
  失敗した場合はセレクトボックスを隠して既定モデルに任せ、UI エラーにはしない。
  接続先の `/models` 取得に失敗し、キャッシュも無い場合は、API が既定モデル
  1 件を返す。
- 選択モデルは送信時に `POST /api/analyses` の `model` フィールドへ渡す。
  結果画面はジョブが実際に使った `model` を表示する。
- API パラメータは `ChatOptionInput`（空文字 = 未指定）で保持し、送信時だけ
  `reasoning_effort` / `temperature` / `max_tokens` を `FormData` に追加する。
  「詳細設定」は折りたたみで、既定は閉じた状態にする。
- モデル選択時に `capabilities` を参照し、`supports_temperature: false` の
  モデルでは Temperature 入力を無効化する。入力済みの値はモデル変更時にクリアし、
  非対応モデルへ temperature を送らない。
- 結果画面はジョブが実際に使ったパラメータと、合計トークン数（取得できた場合は
  コスト）を表示する。画像ごとの使用量は API では返さず、ジョブ合計のみ扱う。
- 判定中はサーバーが返す `eta_seconds` を「残り時間: 約 X 分（概算）」として
  表示する。未算出時は「計算中…」、完了後は非表示にする。
- アップロード後はサーバーが返す画像 URL を表示する。
- ポーリングは `AbortController` と `setTimeout` で管理し、ジョブ変更・
  アンマウント時に停止する。
- 実行中は結果画面に留まる限り再送信できない。「別の画像で判定」で初期画面に
  戻れるが、その時点ではサーバー上のジョブを削除せず、判定は継続する。
- マウント時に `GET /api/analyses` で履歴を取得し、初期画面の「過去の判定」に
  表示する。履歴を選ぶと既存ジョブを開き、同じ結果画面で表示する。
- 実行ログは「実行ログを表示」で `GET /api/analyses/{id}/logs` を呼び、
  タイムスタンプ付きで表示する。ジョブを開き直したときは再取得する。
- 「別の画像で判定」は履歴を削除しない。履歴の × だけが
  `DELETE /api/analyses/{id}` を呼ぶ。
- ステータスに対応する CSS クラスは `status-cat` / `status-no_cat` /
  `status-error` / `status-running` / `status-pending`。

## セキュリティと運用上の割り切り

- 認証・認可はない。ローカルまたは信頼済みネットワークでの利用を前提とする。
- アップロード画像は外部 LLM API に送信される。機密画像の利用可否は利用者が判断する。
- エラーメッセージには LLM 例外の内容を含む（最大 300 文字）。ローカル用途の
  デバッグ性を優先した割り切りで、公開サービスにする場合はサニタイズが必要。
- 履歴・画像・実行ログは SQLite に残る。DB ファイルにはアップロード画像が
  そのまま入るため、`DATABASE_PATH` のバックアップに注意する。アプリが作成する
  DB 本体とサイドカーは `0600`、新規ディレクトリは `0700` だが、既存ディレクトリの
  権限は自動変更しない。
- 履歴は自動削除しない。ディスク使用量は `DATABASE_PATH` のファイルで増える。

## 拡張の候補

- 失敗画像だけの再判定
- SSE / WebSocket による進捗配信
- 履歴の検索・ページング UI
- 画像のクライアント側リサイズによる転送量削減
- 複数モデルの比較・設定画面
