import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  deleteAnalysis,
  fetchModels,
  getAnalysisLogs,
  listAnalyses,
  type AnalysisListItem,
  type ChatOptionInput,
  type ChatOptions,
  type LogEntry,
  type ModelCapabilities,
} from './api'
import { HistoryList } from './components/HistoryList'
import { ResultGrid } from './components/ResultGrid'
import { SummaryBar, type ResultFilter } from './components/SummaryBar'
import { UploadPanel } from './components/UploadPanel'
import { useAnalysis } from './useAnalysis'
import './App.css'

const MAX_FILES = 500
const MAX_BYTES = 15 * 1024 * 1024
const ALLOWED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

interface SelectedImage {
  file: File
  preview: string
}

function fileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`
}

const EFFORT_LABELS: Record<string, string> = {
  none: 'なし',
  minimal: '最小',
  low: '低',
  medium: '中',
  high: '高',
}

function formatNumber(value: number): string {
  return value.toLocaleString('ja-JP')
}

function formatCost(value: number): string {
  if (value === 0) {
    return '$0.00'
  }
  if (value < 0.01) {
    return `$${value.toFixed(6)}`
  }
  return `$${value.toFixed(4)}`
}

function formatEta(seconds: number): string {
  const total = Math.max(1, Math.round(seconds))
  if (total < 60) {
    return `約 ${total} 秒`
  }
  const minutes = Math.ceil(total / 60)
  if (minutes < 60) {
    return `約 ${minutes} 分`
  }
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest === 0 ? `約 ${hours} 時間` : `約 ${hours} 時間 ${rest} 分`
}

function formatOptions(options: ChatOptions | null | undefined): string | null {
  if (!options) {
    return null
  }
  const parts: string[] = []
  if (options.reasoning_effort) {
    parts.push(`推論: ${EFFORT_LABELS[options.reasoning_effort] ?? options.reasoning_effort}`)
  }
  if (options.temperature !== null) {
    parts.push(`temperature: ${options.temperature}`)
  }
  if (options.max_tokens !== null) {
    parts.push(`最大出力: ${formatNumber(options.max_tokens)}`)
  }
  return parts.length > 0 ? parts.join(' / ') : null
}

export default function App() {
  const [selected, setSelected] = useState<SelectedImage[]>([])
  const [filter, setFilter] = useState<ResultFilter>('all')
  const [clientError, setClientError] = useState<string | null>(null)
  const [models, setModels] = useState<string[] | null>(null)
  const [model, setModel] = useState<string | null>(null)
  const [capabilities, setCapabilities] = useState<
    Record<string, ModelCapabilities>
  >({})
  const [chatOptions, setChatOptions] = useState<ChatOptionInput>({
    reasoning_effort: '',
    temperature: '',
    max_tokens: '',
  })
  const [history, setHistory] = useState<AnalysisListItem[]>([])
  const [historyVersion, setHistoryVersion] = useState(0)
  const [showLogs, setShowLogs] = useState(false)
  const [logs, setLogs] = useState<LogEntry[] | null>(null)
  const [logsError, setLogsError] = useState<string | null>(null)
  const selectedRef = useRef<SelectedImage[]>([])
  const { jobId, summary, error, starting, start, open, reset } = useAnalysis()

  useEffect(() => {
    const controller = new AbortController()
    fetchModels(controller.signal)
      .then((catalog) => {
        setModels(catalog.models)
        setCapabilities(catalog.capabilities ?? {})
        setModel((current) => current ?? catalog.default)
      })
      .catch(() => {
        // Without a catalog the select stays hidden and the server falls
        // back to the configured model.
      })
    return () => {
      controller.abort()
    }
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    listAnalyses(controller.signal)
      .then(setHistory)
      .catch(() => {
        if (!controller.signal.aborted) {
          setHistory([])
        }
      })
    return () => {
      controller.abort()
    }
  }, [historyVersion])

  const files = useMemo(() => selected.map((item) => item.file), [selected])
  const previews = useMemo(() => selected.map((item) => item.preview), [selected])

  useEffect(() => {
    selectedRef.current = selected
  }, [selected])

  useEffect(() => {
    return () => {
      for (const item of selectedRef.current) {
        URL.revokeObjectURL(item.preview)
      }
    }
  }, [])

  const running =
    starting || (jobId !== null && (summary === null || summary.status !== 'completed'))

  const temperatureSupported =
    model === null ? true : capabilities[model]?.supports_temperature ?? true

  const filteredImages = useMemo(() => {
    if (summary === null) {
      return []
    }
    if (filter === 'all') {
      return summary.images
    }
    return summary.images.filter((image) => image.status === filter)
  }, [filter, summary])

  const addFiles = (incoming: File[]) => {
    const accepted: File[] = []
    const existing = new Set(files.map(fileKey))
    let rejectedType = false
    let rejectedSize = false
    let rejectedDuplicate = false

    for (const file of incoming) {
      if (file.type !== '' && !ALLOWED_TYPES.has(file.type)) {
        rejectedType = true
        continue
      }
      if (file.size > MAX_BYTES) {
        rejectedSize = true
        continue
      }
      const key = fileKey(file)
      if (existing.has(key)) {
        rejectedDuplicate = true
        continue
      }
      existing.add(key)
      accepted.push(file)
    }

    const room = Math.max(0, MAX_FILES - files.length)
    const next = accepted.slice(0, room)
    if (next.length > 0) {
      const additions = next.map((file) => ({
        file,
        preview: URL.createObjectURL(file),
      }))
      setSelected((current) => [...current, ...additions])
    }

    const messages: string[] = []
    if (rejectedType) messages.push('JPEG / PNG / WebP 以外のファイルは追加できません。')
    if (rejectedSize) messages.push('15MB を超える画像は追加できません。')
    if (rejectedDuplicate) messages.push('同じ画像は重複して追加できません。')
    if (accepted.length > room) messages.push(`一度に追加できるのは ${MAX_FILES} 枚までです。`)
    setClientError(messages.length > 0 ? messages.join(' ') : null)
  }

  const removeFile = (index: number) => {
    const target = selected[index]
    if (target) {
      URL.revokeObjectURL(target.preview)
    }
    setSelected((current) => current.filter((_, position) => position !== index))
  }

  const handleModelChange = (nextModel: string) => {
    setModel(nextModel)
    if (capabilities[nextModel]?.supports_temperature === false) {
      setChatOptions((current) =>
        current.temperature === '' ? current : { ...current, temperature: '' },
      )
    }
  }

  const handleStart = async () => {
    if (files.length === 0) {
      return
    }
    setFilter('all')
    setClientError(null)
    setShowLogs(false)
    setLogs(null)
    setLogsError(null)
    await start(files, model, chatOptions)
  }

  const handleOpenHistory = (id: string) => {
    setFilter('all')
    setClientError(null)
    setShowLogs(false)
    setLogs(null)
    setLogsError(null)
    open(id)
  }

  const handleDeleteHistory = async (id: string) => {
    try {
      await deleteAnalysis(id)
    } catch {
      // Already removed; refreshing the list is still the right outcome.
    }
    if (jobId === id) {
      reset()
      setShowLogs(false)
      setLogs(null)
      setLogsError(null)
    }
    setHistoryVersion((value) => value + 1)
  }

  const handleNewAnalysis = () => {
    for (const item of selected) {
      URL.revokeObjectURL(item.preview)
    }
    reset()
    setSelected([])
    setFilter('all')
    setClientError(null)
    setShowLogs(false)
    setLogs(null)
    setLogsError(null)
    setHistoryVersion((value) => value + 1)
  }

  const handleToggleLogs = useCallback(async () => {
    if (showLogs) {
      setShowLogs(false)
      return
    }
    setShowLogs(true)
    if (jobId === null) {
      return
    }
    setLogsError(null)
    setLogs(null)
    try {
      const result = await getAnalysisLogs(jobId)
      setLogs(result.logs)
    } catch (caught) {
      setLogsError(
        caught instanceof Error ? caught.message : '実行ログを取得できませんでした。',
      )
    }
  }, [jobId, showLogs])

  const visibleError = clientError ?? error
  const usedOptions = summary === null ? null : formatOptions(summary.options)

  return (
    <div className="app-shell">
      <header className="site-header">
        <div className="brand">
          <span aria-hidden="true">🐾</span>
          <h1>catfinder</h1>
        </div>
        <p className="tagline">複数の画像から、猫が写っているものを見つけます。</p>
        <p className="powered-by">LangChain + vision model</p>
      </header>

      <main>
        {visibleError && (
          <p className="alert" role="alert">
            {visibleError}
          </p>
        )}

        {jobId === null ? (
          <>
            <UploadPanel
              files={files}
              previews={previews}
              starting={starting}
              disabled={starting}
              models={models}
              model={model}
              options={chatOptions}
              temperatureSupported={temperatureSupported}
              onModelChange={handleModelChange}
              onOptionsChange={setChatOptions}
              onAdd={addFiles}
              onRemove={removeFile}
              onStart={handleStart}
            />
            <HistoryList
              items={history}
              onOpen={handleOpenHistory}
              onDelete={handleDeleteHistory}
            />
          </>
        ) : (
          <section className="results-view" aria-labelledby="results-heading">
            <div className="results-heading">
              <div>
                <h2 id="results-heading">判定結果</h2>
                <p>
                  {summary === null
                    ? 'アップロードを受け付けました。判定を開始しています…'
                    : `${summary.completed} / ${summary.total} 枚を判定しました。`}
                </p>
                {summary?.status !== 'completed' && (
                  <p className="eta">
                    残り時間:{' '}
                    {summary === null || summary.eta_seconds == null
                      ? '計算中…'
                      : `${formatEta(summary.eta_seconds)}（概算）`}
                  </p>
                )}
                {summary?.model && <p className="model-used">判定モデル: {summary.model}</p>}
                {usedOptions && <p className="options-used">設定: {usedOptions}</p>}
                {summary?.usage && (
                  <p className="token-usage">
                    使用トークン: {formatNumber(summary.usage.total_tokens)}
                    {summary.usage.cost !== null &&
                      `（コスト: ${formatCost(summary.usage.cost)}）`}
                  </p>
                )}
              </div>
              <button type="button" className="secondary" onClick={handleNewAnalysis}>
                別の画像で判定
              </button>
            </div>

            <div
              className={`progress${summary === null ? ' is-indeterminate' : ''}`}
              role="progressbar"
              aria-label="判定の進捗"
              aria-valuemin={0}
              aria-valuemax={summary?.total ?? 100}
              aria-valuenow={summary?.completed ?? undefined}
            >
              <span
                style={{
                  width:
                    summary === null || summary.total === 0
                      ? '35%'
                      : `${Math.round((summary.completed / summary.total) * 100)}%`,
                }}
              />
            </div>

            {summary !== null && (
              <>
                <SummaryBar summary={summary} filter={filter} onFilter={setFilter} />
                <ResultGrid
                  images={filteredImages}
                  emptyMessage={
                    running ? '判定中です。完了までお待ちください。' : '該当する画像はありません。'
                  }
                />
              </>
            )}

            <div className="logs-actions">
              <button
                type="button"
                className="secondary"
                onClick={handleToggleLogs}
                disabled={summary === null}
              >
                {showLogs ? '実行ログを閉じる' : '実行ログを表示'}
              </button>
            </div>

            {showLogs && (
              <section className="logs-panel" aria-labelledby="logs-heading">
                <h3 id="logs-heading">実行ログ</h3>
                {logsError && <p className="error-text">{logsError}</p>}
                {logs === null && !logsError && <p className="empty-message">読み込み中…</p>}
                {logs !== null && logs.length === 0 && (
                  <p className="empty-message">実行ログはありません。</p>
                )}
                {logs !== null && logs.length > 0 && (
                  <ol className="log-list">
                    {logs.map((entry) => (
                      <li key={entry.id} className={`log-entry log-${entry.level}`}>
                        <time dateTime={new Date(entry.created_at * 1000).toISOString()}>
                          {new Date(entry.created_at * 1000).toLocaleTimeString('ja-JP')}
                        </time>
                        <span>{entry.message}</span>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            )}
          </section>
        )}
      </main>

      <footer className="site-footer">
        <p>
          判定結果・画像・実行ログはサーバーの SQLite に保存され、ブラウザを閉じても
          過去の判定から開けます。不要になった履歴は削除できます。
        </p>
      </footer>
    </div>
  )
}
