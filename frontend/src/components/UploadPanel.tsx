import { useRef, useState } from 'react'

import type { ChatOptionInput } from '../api'

interface UploadPanelProps {
  files: File[]
  previews: string[]
  starting: boolean
  disabled: boolean
  models: string[] | null
  model: string | null
  options: ChatOptionInput
  temperatureSupported: boolean
  onModelChange: (model: string) => void
  onOptionsChange: (options: ChatOptionInput) => void
  onAdd: (files: File[]) => void
  onRemove: (index: number) => void
  onStart: () => void
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} KB`
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function UploadPanel({
  files,
  previews,
  starting,
  disabled,
  models,
  model,
  options,
  temperatureSupported,
  onModelChange,
  onOptionsChange,
  onAdd,
  onRemove,
  onStart,
}: UploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)

  const handleDrop = (event: React.DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (!disabled) {
      onAdd(Array.from(event.dataTransfer.files))
    }
  }

  return (
    <section className="upload-panel" aria-labelledby="upload-heading">
      <div className="panel-heading">
        <div>
          <h2 id="upload-heading">画像を選ぶ</h2>
          <p>猫が写っているか判定したい画像をまとめて追加してください。</p>
        </div>
        <span className="file-count">{files.length} 枚</span>
      </div>

      <div
        className={`drop-zone${dragging ? ' is-dragging' : ''}`}
        onDragOver={(event) => {
          event.preventDefault()
          if (!disabled) {
            setDragging(true)
          }
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        <input
          ref={inputRef}
          type="file"
          aria-label="画像を選択"
          accept="image/jpeg,image/png,image/webp"
          multiple
          hidden
          disabled={disabled}
          onChange={(event) => {
            onAdd(Array.from(event.target.files ?? []))
            event.target.value = ''
          }}
        />
        <p>ここに画像をドラッグ＆ドロップ</p>
        <button
          type="button"
          className="secondary"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
        >
          ファイルを選択
        </button>
        <small>JPEG / PNG / WebP、最大500枚・1枚15MBまで</small>
      </div>

      {files.length > 0 && (
        <ul className="file-list">
          {files.map((file, index) => (
            <li key={`${file.name}-${file.lastModified}-${index}`}>
              <img src={previews[index]} alt="" />
              <div className="file-meta">
                <span className="file-name">{file.name}</span>
                <span className="file-size">{formatSize(file.size)}</span>
              </div>
              <button
                type="button"
                className="icon-button"
                aria-label={`${file.name} を削除`}
                disabled={disabled}
                onClick={() => onRemove(index)}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      {models !== null && models.length > 0 && (
        <div className="model-select">
          <label htmlFor="model-select">判定モデル</label>
          <select
            id="model-select"
            value={model ?? models[0]}
            disabled={disabled}
            onChange={(event) => onModelChange(event.target.value)}
          >
            {models.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </div>
      )}

      <details className="advanced-options">
        <summary>詳細設定</summary>
        <div className="options-grid">
          <label htmlFor="reasoning-effort">推論の強さ</label>
          <select
            id="reasoning-effort"
            value={options.reasoning_effort}
            disabled={disabled}
            onChange={(event) =>
              onOptionsChange({ ...options, reasoning_effort: event.target.value })
            }
          >
            <option value="">既定</option>
            <option value="none">なし</option>
            <option value="minimal">最小</option>
            <option value="low">低</option>
            <option value="medium">中</option>
            <option value="high">高</option>
          </select>

          <label htmlFor="temperature">Temperature</label>
          <input
            id="temperature"
            type="number"
            min="0"
            max="2"
            step="0.1"
            placeholder={temperatureSupported ? '0（既定）' : 'このモデルは非対応'}
            value={options.temperature}
            disabled={disabled || !temperatureSupported}
            aria-describedby={
              temperatureSupported ? undefined : 'temperature-unsupported'
            }
            onChange={(event) =>
              onOptionsChange({ ...options, temperature: event.target.value })
            }
          />

          <label htmlFor="max-tokens">最大出力トークン</label>
          <input
            id="max-tokens"
            type="number"
            min="1"
            step="1"
            placeholder="未指定"
            value={options.max_tokens}
            disabled={disabled}
            onChange={(event) =>
              onOptionsChange({ ...options, max_tokens: event.target.value })
            }
          />
        </div>
        {!temperatureSupported && (
          <p className="options-hint" id="temperature-unsupported">
            このモデルは Temperature に対応していません。
          </p>
        )}
        <p className="options-hint">
          モデルやプロバイダが対応していないパラメータを指定すると、判定がエラーになる場合があります。
        </p>
      </details>

      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={disabled || starting || files.length === 0}
          onClick={onStart}
        >
          {starting ? '送信中…' : '判定する'}
        </button>
      </div>
    </section>
  )
}
