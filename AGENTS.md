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

- `.env` はコミットしない。テンプレートは `.env.example`。
- `LLM_MODEL` 未設定でも起動できる。判定 API は 503、`GET /api/health` で設定有無を確認。
- `.env` は pydantic-settings がカレントディレクトリから読む。サーバーはリポジトリルートで起動する。

## 構造の要点

- `app/main.py` の `create_app(settings, analyzer)` が組み立てとテスト注入点。
  モジュール末尾の `app = create_app()` は import 時に `.env` を読む。
- ジョブはインメモリ (`app/jobs.py`)。DB・マイグレーションはない。TTL で画像ごと削除。
- 画像は Pillow で実データを検証し、UUID 名で一時保存。元のファイル名は表示専用。
- `app/analyzer.py` は LangChain の標準 content block で base64 画像を送信し、
  `CatVerdict` に structured output する。`LLM_STRUCTURED_OUTPUT=auto` は
  `json_schema` 失敗時に JSON パースへフォールバックする。
- 画像 1 枚の失敗は `error` として記録し、ジョブは継続。`completed` は成功を意味しない。
- フロントエンドは `/api` を相対パスで呼ぶ。Vite proxy 前提なので CORS を追加しない。
- `frontend/src/useAnalysis.ts` が 1 秒ポーリング。`AbortController` と timer の後始末を壊さない。
- 画像選択の object URL は追加時に生成し、削除・リセット・アンマウント時に revoke する。
- ステータスと CSS クラスは `status-cat` / `status-no_cat` / `status-error` に対応する。

## サブエージェント

- `auto-committer` (`.opencode/agents/auto-committer.md`):
  呼び出された時点の未コミット変更を `commit` スキルでコミットする。
  ファイル編集・`git push`・ブランチ操作は行わない。

## 変更時の注意

- API の形を変えたら `frontend/src/api.ts`、`tests/`、`docs/details.md` を同時に更新する。
- UI 文言は日本語。`frontend/src/App.test.tsx` は日本語ラベルで要素を取得している。
- 新しい環境変数は `.env.example`、`README.md`、`docs/details.md` に追加する。
- 実装を変えたら `README.md` / `docs/design.md` / `docs/details.md` を追従させる。
- 生成物をコミットしない: `.venv/`, `frontend/node_modules/`, `frontend/dist/`,
  `.pytest_cache/`, `.ruff_cache/`, `.env`, `*.pid`, `*.log`, 一時画像。

## 関連ドキュメント

| ファイル | 内容 |
| --- | --- |
| `README.md` | 利用者向けのセットアップ・起動・API 概要 |
| `docs/design.md` | 設計・データフロー・判断理由 |
| `docs/details.md` | API 仕様・テスト・制約 |
| `handover.md` | 現状と未検証事項 |
