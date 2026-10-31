import type { ImageResult, ImageStatus } from '../api'

interface ResultGridProps {
  images: ImageResult[]
  emptyMessage: string
}

const LABELS: Record<ImageStatus, string> = {
  pending: '待機中',
  running: '判定中',
  cat: '猫あり',
  no_cat: '猫なし',
  error: 'エラー',
}

export function ResultGrid({ images, emptyMessage }: ResultGridProps) {
  if (images.length === 0) {
    return <p className="empty-message">{emptyMessage}</p>
  }

  return (
    <ul className="result-grid">
      {images.map((image) => (
        <li key={image.id} className={`result-card status-${image.status}`}>
          <div className="thumb">
            <img src={image.url} alt={image.filename} loading="lazy" />
            <span className={`badge status-${image.status}`}>{LABELS[image.status]}</span>
          </div>
          <div className="result-body">
            <div className="result-title">
              <span className="file-name" title={image.filename}>
                {image.filename}
              </span>
              {image.confidence !== null && (
                <span className="confidence">確信度 {Math.round(image.confidence * 100)}%</span>
              )}
            </div>
            {image.reason && <p className="reason">{image.reason}</p>}
            {image.error && <p className="error-text">{image.error}</p>}
          </div>
        </li>
      ))}
    </ul>
  )
}
