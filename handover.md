# 引き継ぎ

## 現状

- FastAPI + LangChain（`ChatOpenAI`）のバックエンドと、React + Vite の UI を実装済み。
- バックエンド 20 テスト、フロントエンド 4 テストが成功。実 LLM の結合テスト 1 件は既定でスキップ。
- `npm run build`、`npm run lint`、`uv run ruff check .` が成功。
- `scripts/server.sh` / `scripts/ui.sh` で起動・停止を確認済み。
- `/api/health` と Vite proxy 経由の `/api/health` を確認済み。
- OpenAI 互換のモックサーバーを使い、LangChain の構造化出力・画像送信・
  ジョブ完了・画像取得までを結合確認済み。

## 未検証

- 実際の vision LLM に対する判定。`.env` に有効な `LLM_BASE_URL` /
  `LLM_API_KEY` / `LLM_MODEL` を設定し、猫画像と非猫画像で確認する必要がある。
  `RUN_LLM_INTEGRATION=1 LLM_INTEGRATION_IMAGE=... uv run pytest tests/test_integration.py`
  で確認できる。
- 採用モデルが `json_schema` 構造化出力に対応しているか。
  未対応の場合は `LLM_STRUCTURED_OUTPUT=json` を試す。
- 実ブラウザでのドラッグ＆ドロップと大量画像時の操作性。

## 次の候補

1. 実 LLM での結合確認とプロンプト調整
2. 失敗画像だけの再判定
3. SSE による進捗配信
4. 履歴が必要になった場合の SQLite 導入
5. クライアント側での画像リサイズ

## 注意

- 結果はインメモリ。再起動で消える。
- `LLM_MODEL` 未設定時は判定 API が 503 を返す（起動自体は成功する）。
- テストは `FakeAnalyzer` を使うため、実 API キーは不要。
