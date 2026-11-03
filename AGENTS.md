# AGENTS.md

catfinder は、複数の画像から猫が写っている画像を vision LLM で判定する Web アプリ。
FastAPI + LangChain (`ChatOpenAI`) / React + TypeScript + Vite 構成。UI 文言は日本語。

## コマンド

```bash
uv sync
uv run pytest
uv run pytest tests/test_api.py::test_mixed_results_and_image_serving
uv run ruff check .

cd frontend
npm test          # vitest run
npm run lint      # oxlint
npm run build     # tsc -b && vite build

scripts/server.sh start|stop|restart|status   # API: 8000
scripts/ui.sh start|stop|restart|status       # UI : 5173
```

- Python は **uv** のみ（`pip` を直接使わない）。Python 3.14。
- フロントエンドは npm。
- 実 LLM はテストで呼ばない。`tests/conftest.py` の `FakeAnalyzer` を注入する。
- API / UI は `scripts/*.sh` 経由で起動する（uvicorn / vite を直接常駐させない）。

## 環境

- `.env` はコミットしない。`.gitignore` は `.env*` を無視し、
  `.env.example` だけを追跡する。テンプレートは `.env.example`。
- `LLM_MODEL` 未設定でも起動できる。判定 API は 503、`GET /api/health` で設定有無を確認。
- `.env` は pydantic-settings がカレントディレクトリから読む。サーバーはリポジトリルートで起動する。
- `DATABASE_PATH`（既定 `./data/catfinder.db`）に履歴・画像・実行ログが残る。
  DB 本体と WAL / SHM / journal は `0600`、新規作成する保存先ディレクトリは
  `0700`。`scripts/server.sh` は `umask 077` で起動する。
  テストは `tmp_path` の DB を使うため、ローカルの履歴を汚さない。

## 構造の要点

- `app/main.py` の `create_app(settings, analyzer, catalog)` が組み立てとテスト注入点。
  モジュール末尾の `app = create_app()` は import 時に `.env` を読む。
- ジョブ・判定結果・画像 (BLOB)・実行ログは SQLite (`app/storage.py`, `DATABASE_PATH`) に
  永続化する。自動削除はなく、`DELETE /api/analyses/{id}` でジョブごと削除する。
- 実行中のジョブはメモリ (`app/jobs.py`) にも保持する。履歴は SQLite から読むため、
  プロセス再起動後も `GET /api/analyses/{id}` で同じ結果を取得できる。
  再起動時に未完了ジョブは中断扱いにする。
- 画像は Pillow で実データを検証し、UUID 名で作業ディレクトリに保存。DB には BLOB で
  保存し、完了後に作業ディレクトリを削除する。元のファイル名は表示専用。
- 画面は `GET /api/analyses` の履歴から過去の判定を開ける。実行ログは
  `GET /api/analyses/{id}/logs` で取得する。
- `app/analyzer.py` は LangChain の標準 content block で base64 画像を送信し、
  `CatVerdict` に常に `json_schema` の structured output で出力させる。
  JSON パースへのフォールバックはない。
- `app/models.py` は `GET /models` から vision かつ `structured_outputs` 対応の
  モデルだけを返す。`LLM_MODEL` が一覧に無い場合は一覧先頭を既定にする。
  `supported_parameters` から temperature / reasoning_effort の対応状況も抽出し、
  未知の接続先は「対応」として扱う。
- API パラメータ (`reasoning_effort` / `temperature` / `max_tokens`) はジョブに保存し、
  モデルと実効パラメータの組で `ChatOpenAI` をキャッシュする。キャッシュは LRU で
  上限 `MAX_CACHED_ANALYZERS = 32` 件。あふれたクライアントは使用中リクエストが
  終わってから `aclose()` し、シャットダウン時にも解放する。temperature 非対応
  モデルには送らず、明示指定は 400。UI は Temperature 入力を無効化する。
  各 `ChatOpenAI` には専用の `httpx.Client` / `httpx.AsyncClient` を渡し、
  langchain-openai の共有 HTTP クライアントを閉じない。
- 使用量は `include_raw=True` の raw `usage_metadata` から取得する。API はジョブ合計のみ
  返し、UI は合計トークン数（取得できればコスト）を表示する。
- 画像 1 枚の失敗は `error` として記録し、ジョブは継続。`completed` は成功を意味しない。
- フロントエンドは `/api` を相対パスで呼ぶ。Vite proxy 前提なので CORS を追加しない。
- `frontend/src/useAnalysis.ts` が 1 秒ポーリング。`AbortController` と timer の後始末を壊さない。
- 画像選択の object URL は追加時に生成し、削除・リセット・アンマウント時に revoke する。
- ステータスと CSS クラスは `status-cat` / `status-no_cat` / `status-error` /
  `status-running` / `status-pending` に対応する。

## サブエージェントとプラグイン

- `auto-committer` (`~/.config/opencode/agents/auto-committer.md`):
  global の自動コミット担当サブエージェント。呼び出された時点の未コミット変更を
  `commit` スキルでコミットする。ファイル編集・`git push`・ブランチ操作は行わない。
  このリポジトリの `.opencode/` には置かない。
- `docs-updater` (`~/.config/opencode/agents/docs-updater.md`):
  global のドキュメント更新サブエージェント。呼び出された時点の実装変更に
  合わせて、global の `project-docs` スキルで `README.md` / `docs/` /
  `handover.md` / `AGENTS.md` を最新化する。実装コードは編集せず、
  コミットもしない（`auto-committer` が担当）。このリポジトリの
  `.opencode/` には置かない。
- `script-runner` (`~/.config/opencode/agents/script-runner.md`):
  global のスクリプト実行サブエージェント。プロジェクトの `scripts/` 配下の
  スクリプトを、依頼された引数の通りに実行して結果を報告する。解析・
  ファイル編集はしない。このリポジトリの `.opencode/` には置かない。
- `auto-commit-on-build` (`~/.config/opencode/plugins/auto-commit-on-build/index.ts`):
  global のプラグイン。Build エージェントのトップレベルセッションが
  `session.idle` になったとき、未コミット変更があれば `auto-committer` を
  子セッションで自動実行する。変更がなければ何もしない。
  自動コミットを止めたい場合はこのプラグインを削除するか、`BUILD_AGENT` の判定を変える。
- `auto-docs-on-build` (`~/.config/opencode/plugins/auto-docs-on-build/index.ts`):
  global のプラグイン。Build エージェントのトップレベルセッションが
  `session.idle` になったとき、`docs-updater` を子セッションで自動実行する。
  ドキュメントの変更はコミットせず、`auto-committer` に任せる。更新が不要なら
  `docs-updater` が何も編集せず終了する。自動更新を止めたい場合はこのプラグインを
  削除するか、`BUILD_AGENT` の判定を変える。

## 変更時の注意

- API の形を変えたら `frontend/src/api.ts`、`tests/`、`docs/details.md` を同時に更新する。
- UI 文言は日本語。`frontend/src/App.test.tsx` は日本語ラベルで要素を取得している。
- 新しい環境変数は `.env.example`、`README.md`、`docs/design.md`、`docs/details.md` に追加する。
- 実装を変えたら `README.md` / `docs/design.md` / `docs/details.md` / `handover.md` を追従させる。
- 生成物をコミットしない: `.venv/`, `frontend/node_modules/`, `frontend/dist/`,
  `.pytest_cache/`, `.ruff_cache/`, `.env*`（`.env.example` を除く）, `*.pid`, `*.log`,
  `*.db`, `*.db-*`, `*.sqlite*`, `data/`（SQLite DB）, 一時画像。
- DB の権限 (`0600` / 新規ディレクトリ `0700`)、`scripts/server.sh` の
  `umask 077`、アナライザーの LRU 上限と `aclose()` の解放経路を壊さない。
- DB スキーマを変えるときは `app/storage.py` に後方互換の `CREATE TABLE IF NOT EXISTS` /
  列追加を入れ、既存 `data/catfinder.db` を壊さない。

## 関連ドキュメント

| ファイル | 内容 |
| --- | --- |
| `README.md` | 利用者向けのセットアップ・起動・API 概要 |
| `docs/design.md` | 設計・データフロー・判断理由 |
| `docs/details.md` | API 仕様・テスト・制約 |
| `handover.md` | 現状と未検証事項 |
