# 引き継ぎ

## 現状

- FastAPI + LangChain（`ChatOpenAI`）のバックエンドと、React + Vite の UI を実装済み。
- バックエンド 50 テスト（モデル一覧・モデル指定・API パラメータ・使用量集計・
  ETA・並列数制御を含む）、フロントエンド 10 テストが成功。
  実 LLM の結合テスト 1 件は既定でスキップ。
- 判定リクエストは `LLM_CONCURRENCY`（既定 8、上限 32）で並列化済み。
  `asyncio.Semaphore` により同時 HTTP リクエスト数を制限する。
- 画像は UI・API とも 1 リクエスト 500 枚まで（`MAX_FILES`、上限 500）。
  合計サイズの上限はなく、500 枚 × 15MB で最大 7.5GB になり得る。
- 判定中の残り時間を `eta_seconds` として API が返し、UI が
  「残り時間: 約 X 分（概算）」を表示する。完了 0 枚・完了後は `null`。
- 判定ごとのモデル選択を実装済み。`GET /api/models` が接続先の `/models` から
  vision かつ `structured_outputs` 対応のモデルだけを取得し（600 秒キャッシュ、
  失敗時は既定 1 つにフォールバック）、UI の「判定モデル」セレクトボックス →
  `POST /api/analyses` の `model` フィールドでジョブごとに使用モデルを
  切り替えられる。`LLM_MODEL` が一覧に無い場合は一覧の先頭が既定になる。
- UI の「詳細設定」から `reasoning_effort` / `temperature` / `max_tokens` を
  ジョブごとに指定できる。未指定時は temperature 0、それ以外はプロバイダ既定。
- 判定結果は常に `json_schema` の Structured Output で受け取り、
  `include_raw=True` で `usage_metadata` から使用トークンを取得する。
  JSON パースへのフォールバックはない。OpenRouter 接続時は
  `provider.require_parameters` で Structured Output 対応 endpoint に限定する。
  結果画面にはジョブ合計の使用トークン数と、OpenRouter が返す場合のみコストを表示する。
- `npm run build`、`npm run lint`、`uv run ruff check .` が成功。
- `scripts/server.sh` / `scripts/ui.sh` で起動・停止を確認済み。
- `/api/health` と Vite proxy 経由の `/api/health` を確認済み。
- OpenAI 互換のモックサーバーを使い、LangChain の構造化出力・画像送信・
  ジョブ完了・画像取得までを結合確認済み。

## 未検証

- 実際の vision LLM に対する判定。既定は OpenRouter の
  `nvidia/nemotron-3.5-super-vl:free`（`LLM_BASE_URL=https://openrouter.ai/api/v1`）
  なので、`.env` の `LLM_API_KEY` に OpenRouter のキーを設定し、猫画像と非猫画像で
  確認する必要がある。別モデルに差し替える場合は `LLM_MODEL` を変更する。
  `RUN_LLM_INTEGRATION=1 LLM_INTEGRATION_IMAGE=... uv run pytest tests/test_integration.py`
  で確認できる。
- 既定モデル `nvidia/nemotron-3.5-super-vl:free` が実際に `json_schema`
  構造化出力を受け付けるか（`/models` の `structured_outputs` は確認済み）。
- OpenRouter の実 `/api/models` に対する `GET /api/models`（vision フィルタの
  退屈なケース・フリーモデルの並び順）は未検証。モックでのみ確認済み。
- 選択した別モデルでの実判定。モデルごとの応答特性（confidence の単位や
  構造化出力対応）はモデル依存のため、実キーでの確認が必要。
- 実モデルで `reasoning_effort` / `temperature` / `max_tokens` が受け付けられるか。
  モデルによっては 400 になり、画像単位のエラーになる。
- OpenRouter の `usage`（トークン数・cost）が LangChain の
  `usage_metadata` / `response_metadata` に期待どおり載るか。
- 非対応 endpoint にルーティングされた場合に、`json_schema` が画像単位の
  エラーとして正しく記録されるか。
- 実ブラウザでのドラッグ＆ドロップと大量画像時の操作性。
- 500 枚を選択したときの UI 描画・スクロール性能と、アップロード時間。
- 500 枚アップロード時のディスク使用量（最大 7.5GB）と `UPLOAD_DIR` の空き容量。
- `eta_seconds` の実測精度。画像サイズやモデルの応答時間がばらつくと大きく
  ずれるため、実 LLM で数ジョブ流して確認する。
- `LLM_CONCURRENCY=8` でプロバイダのレート制限（429）に当たらないか。
  当たる場合は下げる。
- 並列数を上げたときのプロバイダ側のスループットと、UI のポーリング負荷。

## 次の候補

1. 実 LLM での結合確認とプロンプト調整
2. 失敗画像だけの再判定
3. SSE による進捗配信
4. 履歴が必要になった場合の SQLite 導入
5. クライアント側での画像リサイズ
6. 画像ごとの使用量表示、コスト上限、モデル別の対応パラメータ表示
7. ETA の精度改善（画像サイズ・過去の実績を加味する）

## 注意

- 結果はインメモリ。再起動で消える。
- `LLM_MODEL` 未設定時は判定 API が 503 を返す（起動自体は成功する）。
- テストは `FakeAnalyzer` を使うため、実 API キーは不要。
- ローカルの `.env` は `LLM_CONCURRENCY=8` に更新済み。`.env` はコミットしない。
