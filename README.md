# catfinder

複数の画像ファイルから、猫が写っている画像を見つける小さな Web アプリです。
FastAPI のバックエンドが LangChain 経由で vision 対応 LLM に判定を依頼し、
React + Vite の UI が進捗と結果を表示します。

## 特長

- 複数画像をまとめてアップロードし、画像ごとに「猫あり / 猫なし / エラー」を表示
- 既定で OpenRouter の vision モデルを使用。OpenAI 互換 API であれば
  OpenAI・Ollama・vLLM・LiteLLM などを `LLM_BASE_URL` / `LLM_MODEL` で切り替え可能
- 判定ごとに vision モデルを選択可能（`/api/models` が Structured Output 対応モデルだけを自動取得）
- 「詳細設定」から推論の強さ（reasoning effort）、temperature、最大出力トークンを指定可能
  （temperature 非対応モデルでは入力欄を自動的に無効化）
- 結果の絞り込み、件数集計、判定理由・確信度、使用トークン数（取得できた場合はコスト）の表示
- 判定中の残り時間を超概算で表示
- 1 枚の判定が失敗しても、残りの画像の判定は継続
- 判定結果・画像・実行ログを SQLite に保存し、ブラウザやサーバーを再起動しても
  過去の判定を開ける（履歴から明示的に削除するまで保持）

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
UI では判定のたびに、接続先が `/models` で提供する「vision かつ Structured
Output 対応」のモデルから選べます。`LLM_MODEL` がその一覧に無い場合は、
一覧の先頭が既定値として使われます。判定結果は常に JSON Schema
（`json_schema`）による Structured Output で受け取るため、非対応モデルは
一覧に出ません（提供元が `supported_parameters` を返さない場合は判定できず、
従来どおり全 vision モデルが候補になります）。

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

API のポートを変える場合は、`frontend/vite.config.ts` の `server.proxy` の
`target` も同じポートに変更してください。

## 使い方

1. 画像をドラッグ＆ドロップ、または「ファイルを選択」から追加
2. 「判定モデル」で使用する vision + Structured Output 対応モデルを選ぶ
   （省略時は `LLM_MODEL`、一覧に無ければ一覧の先頭）
3. 必要に応じて「詳細設定」で推論の強さ・temperature・最大出力トークンを指定
4. 「判定する」を押す
5. 進捗バー・件数・残り時間（概算）が更新され、完了後に結果カードが表示される
6. 「すべて / 猫あり / 猫なし / エラー」で絞り込む
7. 「実行ログを表示」でジョブの実行ログを確認する
8. 「別の画像で判定」で最初に戻る。結果は「過去の判定」に残り、
   ブラウザを閉じた後も開ける。不要な履歴は × で削除する

対応形式は JPEG / PNG / WebP、最大 500 枚・1 枚 15MB です。
LLM の確信度はモデルの自己申告値であり、校正された確率ではありません。

### API パラメータと使用トークン

「詳細設定」で指定できる値:

| 項目 | リクエストフィールド | 値 | 未指定時 |
| --- | --- | --- | --- |
| 推論の強さ | `reasoning_effort` | `none` / `minimal` / `low` / `medium` / `high` | プロバイダ既定 |
| Temperature | `temperature` | 0〜2 | 0（モデルが対応している場合） |
| 最大出力トークン | `max_tokens` | 1〜200000 | プロバイダ既定 |

結果画面には、判定に成功した画像の合計トークン数を表示します。OpenRouter が
`usage.cost` を返した場合はコストも表示します。プロバイダが使用量を返さない
場合は表示されません。temperature 非対応のモデルでは UI の Temperature が
無効になり、明示的に指定した場合は API が 400 を返します。その他の未対応
パラメータを指定した場合、その画像はエラーになります。

## API

| メソッド | パス | 説明 |
| --- | --- | --- |
| `POST` | `/api/analyses` | 複数画像と任意の `model` / API パラメータを受け取り、判定ジョブを開始（202） |
| `GET` | `/api/analyses/{id}` | 進捗と画像ごとの判定結果 |
| `GET` | `/api/analyses/{id}/images/{image_id}` | 結果表示用の画像 |
| `GET` | `/api/analyses` | 保存済みの判定履歴（新しい順） |
| `GET` | `/api/analyses/{id}/logs` | ジョブの実行ログ |
| `DELETE` | `/api/analyses/{id}` | ジョブ・画像・ログを履歴から削除（204） |
| `GET` | `/api/models` | 選択可能な vision + Structured Output 対応モデルの一覧・既定値・パラメータ対応状況 |
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
| `LLM_CONCURRENCY` | `8` | 同時に送る判定リクエスト数 |
| `MAX_FILES` | `500` | 1 リクエストあたりの最大枚数 |
| `MAX_FILE_MB` | `15` | 1 枚あたりの最大サイズ |
| `DATABASE_PATH` | `./data/catfinder.db` | 結果・画像・実行ログを保存する SQLite |
| `UPLOAD_DIR` | 一時ディレクトリ | 判定中だけ使う作業ディレクトリ。完了後に削除される |

UI 側の上限は 500 枚 / 1 枚 15MB の固定値です。`MAX_FILES` / `MAX_FILE_MB` を
変更する場合は `frontend/src/App.tsx` の定数も合わせて変更してください。

判定結果は常に `json_schema` の Structured Output で受け取ります。自由形式の
JSON 応答をパースするフォールバックはなく、非対応のモデル・endpoint では
その画像がエラーになります。OpenRouter 接続時は `provider.require_parameters`
を送り、Structured Output 対応 endpoint にのみルーティングします。temperature
はモデルが対応している場合だけ送信し、未指定時は 0 を使います。
`LLM_CONCURRENCY` を大きくすると速くなりますが、プロバイダのレート制限
（429）に当たりやすくなります。残り時間は完了枚数と経過時間から求める
超概算で、判定中に変動します。

### 永続化と履歴

- ジョブ、判定結果、画像（BLOB）、実行ログを `DATABASE_PATH` の SQLite に保存します。
- 画像は判定中だけ `UPLOAD_DIR`（未指定なら一時ディレクトリ）に書き出し、
  完了後に作業ディレクトリを削除します。表示は SQLite から行います。
- ブラウザを閉じたり API を再起動しても、`GET /api/analyses/{id}` と
  `GET /api/analyses/{id}/images/{image_id}` で同じ結果を取得できます。
- 履歴は自動削除しません。`DELETE /api/analyses/{id}` または UI の × で
  明示的に削除してください。
- 正常終了時は未完了の画像を「判定が中断されました。」、異常終了後の再起動時は
  「サーバー再起動により中断されました。」としてエラーにし、ジョブは完了扱いに
  します。実行ログにも中断を記録します。

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
  analyzer.py   LangChain による画像判定（モデル・パラメータごとにクライアントを LRU でキャッシュ、上限 32 件）
  jobs.py       ジョブ管理・並列実行・実行ログ記録
  storage.py    SQLite へのジョブ・結果・画像・ログの永続化
  domain.py     ジョブと画像の内部データ構造
  images.py     画像検証と作業ディレクトリへの保存
  schemas.py    API / LLM 出力の型
frontend/
  src/api.ts            API クライアント
  src/useAnalysis.ts    アップロードとポーリング
  src/components/       UI 部品
tests/                  バックエンドのテスト
scripts/                開発用の起動・停止スクリプト
```

自動コミット・自動ドキュメント更新・スクリプト実行（`auto-committer` /
`auto-commit-on-build` / `docs-updater` / `auto-docs-on-build` / `script-runner`）
は global の `~/.config/opencode/` にあり、このリポジトリには含まれません。

## 注意事項

- 認証はありません。信頼できるローカル環境での利用を想定しています。
- 判定結果・画像・実行ログは `DATABASE_PATH` の SQLite に保存されます。
  API を再起動しても履歴から同じ結果を開けます。
- SQLite の DB 本体と WAL / SHM / journal は所有者のみ読み書きできる
  `0600`、新しく作る保存先ディレクトリは `0700` で作成されます。
  `scripts/server.sh` は `umask 077` で起動するため、新規のログや PID ファイルも
  所有者だけが読めます。
- 画像は判定中だけ `UPLOAD_DIR`（未指定時は OS の一時ディレクトリ）に書き出され、
  ジョブ完了後に削除されます。表示は SQLite の BLOB から行います。
- 履歴は自動削除されないため、DB のサイズは `DATABASE_PATH` のファイルで
  確認してください。不要な履歴は × で削除できます。
- 機密画像を外部の LLM API へ送信する場合は、データの扱いを確認してください。
- HEIC / AVIF / GIF には対応していません。
