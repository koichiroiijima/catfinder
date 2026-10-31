import { useEffect, useMemo, useRef, useState } from 'react'

import { deleteAnalysis } from './api'
import { ResultGrid } from './components/ResultGrid'
import { SummaryBar, type ResultFilter } from './components/SummaryBar'
import { UploadPanel } from './components/UploadPanel'
import { useAnalysis } from './useAnalysis'
import './App.css'

const MAX_FILES = 50
const MAX_BYTES = 15 * 1024 * 1024
const ALLOWED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp'])

interface SelectedImage {
  file: File
  preview: string
}

function fileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`
}

export default function App() {
  const [selected, setSelected] = useState<SelectedImage[]>([])
  const [filter, setFilter] = useState<ResultFilter>('all')
  const [clientError, setClientError] = useState<string | null>(null)
  const selectedRef = useRef<SelectedImage[]>([])
  const { jobId, summary, error, starting, start, reset } = useAnalysis()

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

  const handleStart = async () => {
    if (files.length === 0) {
      return
    }
    setFilter('all')
    setClientError(null)
    await start(files)
  }

  const handleReset = async () => {
    if (jobId !== null) {
      try {
        await deleteAnalysis(jobId)
      } catch {
        // The server may already have discarded the job; local reset still wins.
      }
    }
    for (const item of selected) {
      URL.revokeObjectURL(item.preview)
    }
    reset()
    setSelected([])
    setFilter('all')
    setClientError(null)
  }

  const visibleError = clientError ?? error

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
          <UploadPanel
            files={files}
            previews={previews}
            starting={starting}
            disabled={starting}
            onAdd={addFiles}
            onRemove={removeFile}
            onStart={handleStart}
          />
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
              </div>
              <button type="button" className="secondary" onClick={handleReset}>
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
          </section>
        )}
      </main>

      <footer className="site-footer">
        <p>結果は一定時間後にサーバーから削除されます。</p>
      </footer>
    </div>
  )
}
