# catfinder

複数の画像ファイルから、猫が写っている画像を見つける小さな Web アプリです。
FastAPI のバックエンドが LangChain 経由で vision 対応 LLM に判定を依頼し、
React + Vite の UI が進捗と結果を表示します。

## 特長

- 複数画像をまとめてアップロードし、画像ごとに「猫あり / 猫なし / エラー」を表示
- 既定で OpenRouter の vision モデルを使用。OpenAI 互換 API であれば
  OpenAI・Ollama・vLLM・LiteLLM などを `LLM_BASE_URL` / `LLM_MODEL` で切り替え可能
- 判定ごとに vision モデルを選択可能（選択肢は `/api/models` で提供元から自動取得）
- 「詳細設定」から推論の強さ（reasoning effort）、temperature、最大出力トークンを指定可能
- 結果の絞り込み、件数集計、判定理由・確信度、使用トークン数（取得できた場合はコスト）の表示
- 1 枚の判定が失敗しても、残りの画像の判定は継続
- 履歴は持たず、結果と画像は一定時間後に自動削除（ローカル利用向け）

## 必要環境

| ツール | バージョン目安 |
| --- | --- |
| Python | 3.14 以上 |
| uv | 最新版 |
| Node.js / npm | Node.js 22 以上 |
| LLM | 画像入力に対応した OpenAI 互換 API |

Python の依存関係は `pyproject.toml` / `uv.lock`、フロントエンドは
`frontend/package.json` / `frontend/package-lock.json` で管理しています。

## セットアップ

```bash
uv sync
(cd frontend && npm install)
cp .env.example .env
```

`.env` を編集し、`LLM_API_KEY` に OpenRouter の API キーを設定してください。
モデルと接続先は既定で次の値になっています（[OpenRouter](https://openrouter.ai/)）。

```dotenv
LLM_BASE_URL=https://openrouter.ai/api/v1
LLM_API_KEY=replace-with-your-openrouter-key
LLM_MODEL=nvidia/nemotron-3.5-super-vl:free
```

別のモデルを使う場合は `LLM_MODEL` を差し替えます。ローカルの Ollama など
OpenAI 互換サーバーに向ける場合は、次のように 3 つとも上書きします。

```dotenv
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_API_KEY=not-needed
LLM_MODEL=your-vision-model
```

`LLM_MODEL` は「判定 UI でモデルを選ばなかったときの既定値」になります。
UI では判定のたびに、接続先が `/models` で提供する vision 対応モデルから選べます。

## 起動

```bash
scripts/server.sh start   # API: http://127.0.0.1:8000
scripts/ui.sh start       # UI : http://127.0.0.1:5173
```

ブラウザで http://127.0.0.1:5173 を開きます。UI は Vite のプロキシ経由で
`/api` を API サーバーへ転送するため、両方を起動しておいてください。

停止・再起動・状態確認:

```bash
scripts/server.sh stop
scripts/ui.sh stop
scripts/server.sh restart
scripts/ui.sh status
```

スクリプトの環境変数:

| 変数 | 既定値 | 対象 |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | 両方 |
| `PORT` | API: `8000` / UI: `5173` | 各スクリプト |
| `PID_FILE` | `.server.pid` / `.ui.pid` | 各スクリプト |
| `LOG_FILE` | `.server.log` / `.ui.log` | 各スクリプト |
| `FRONTEND_DIR` | `frontend` | UI |

## 使い方

1. 画像をドラッグ＆ドロップ、または「ファイルを選択」から追加
2. 「判定モデル」で使用する vision モデルを選ぶ（省略時は `LLM_MODEL`）
3. 必要に応じて「詳細設定」で推論の強さ・temperature・最大出力トークンを指定
4. 「判定する」を押す
5. 進捗バーと件数が更新され、完了後に結果カードが表示される
6. 「すべて / 猫あり / 猫なし / エラー」で絞り込む
7. 「別の画像で判定」でサーバー上の結果を削除して最初に戻る

対応形式は JPEG / PNG / WebP、最大 50 枚・1 枚 15MB です。
LLM の確信度はモデルの自己申告値であり、校正された確率ではありません。

### API パラメータと使用トークン

「詳細設定」で指定できる値:

| 項目 | リクエストフィールド | 値 | 未指定時 |
| --- | --- | --- | --- |
| 推論の強さ | `reasoning_effort` | `none` / `minimal` / `low` / `medium` / `high` | プロバイダ既定 |
| Temperature | `temperature` | 0〜2 | 0 |
| 最大出力トークン | `max_tokens` | 1〜200000 | プロバイダ既定 |

結果画面には、判定に成功した画像の合計トークン数を表示します。OpenRouter が
`usage.cost` を返した場合はコストも表示します。プロバイダが使用量を返さない
場合は表示されません。指定したパラメータがモデル非対応の場合、その画像は
エラーになります。

## API

| メソッド | パス | 説明 |
| --- | --- | --- |
| `POST` | `/api/analyses` | 複数画像と任意の `model` / API パラメータを受け取り、判定ジョブを開始（202） |
| `GET` | `/api/analyses/{id}` | 進捗と画像ごとの判定結果 |
| `GET` | `/api/analyses/{id}/images/{image_id}` | 結果表示用の画像 |
| `DELETE` | `/api/analyses/{id}` | ジョブと画像を削除（204） |
| `GET` | `/api/models` | 選択可能な vision モデルの一覧と既定値 |
| `GET` | `/api/health` | 稼働状況と LLM 設定有無 |

詳細な仕様は [docs/details.md](docs/details.md)、設計は
[docs/design.md](docs/design.md) を参照してください。
Swagger UI は API 起動中に http://127.0.0.1:8000/docs で確認できます。

## 設定

| 環境変数 | 既定値 | 説明 |
| --- | --- | --- |
| `LLM_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI 互換 API のベース URL |
| `LLM_API_KEY` | `not-needed` | API キー。OpenRouter では必須 |
| `LLM_MODEL` | `nvidia/nemotron-3.5-super-vl:free` | 既定の vision モデル名。空にすると判定 API が 503 |
| `LLM_TIMEOUT_SECONDS` | `60` | 1 枚あたりのタイムアウト |
| `LLM_MAX_RETRIES` | `2` | LangChain 側のリトライ回数 |
| `LLM_CONCURRENCY` | `4` | 同時に判定する画像数 |
| `LLM_STRUCTURED_OUTPUT` | `auto` | `auto` / `function_calling` / `json_schema` / `json` |
| `MAX_FILES` | `50` | 1 リクエストあたりの最大枚数 |
| `MAX_FILE_MB` | `15` | 1 枚あたりの最大サイズ |
| `ANALYSIS_TTL_MINUTES` | `60` | 結果と画像の保持時間 |
| `UPLOAD_DIR` | 一時ディレクトリ | 画像の保存先を固定する場合に指定 |

`LLM_STRUCTURED_OUTPUT=auto` は、まず構造化出力を試し、未対応の
OpenAI 互換サーバーでは JSON 応答のパースに自動フォールバックします。

## テスト

```bash
# バックエンド（通常は実 LLM を呼ばない）
uv run pytest
uv run pytest tests/test_api.py::test_mixed_results_and_image_serving
uv run ruff check .

# 実モデルの確認（.env の設定が必要）
RUN_LLM_INTEGRATION=1 LLM_INTEGRATION_IMAGE=/path/to/cat.jpg \
  uv run pytest tests/test_integration.py -q

# フロントエンド（frontend/ で実行）
npm test
npm run lint
npm run build
```

## 構成

```text
app/
  main.py       FastAPI の組み立てとルーティング
  config.py     環境変数
  models.py     判定モデル一覧の取得とキャッシュ
  analyzer.py   LangChain による画像判定（モデル・パラメータごとにクライアントをキャッシュ）
  jobs.py       インメモリのジョブ管理・並列実行・TTL 削除
  images.py     画像検証と保存
  schemas.py    API / LLM 出力の型
frontend/
  src/api.ts            API クライアント
  src/useAnalysis.ts    アップロードとポーリング
  src/components/       UI 部品
tests/                  バックエンドのテスト
scripts/                開発用の起動・停止スクリプト
```

## 注意事項

- 認証はありません。信頼できるローカル環境での利用を想定しています。
- 判定結果はプロセス内メモリに保持されます。再起動すると消えます。
- 画像は `UPLOAD_DIR` 未指定時、OS の一時ディレクトリに保存されます。
- 機密画像を外部の LLM API へ送信する場合は、データの扱いを確認してください。
- HEIC / AVIF / GIF には対応していません。
