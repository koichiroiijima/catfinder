import type { AnalysisListItem } from '../api'

interface HistoryListProps {
  items: AnalysisListItem[]
  onOpen: (id: string) => void
  onDelete: (id: string) => void
}

function formatDate(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString('ja-JP')
}

export function HistoryList({ items, onOpen, onDelete }: HistoryListProps) {
  if (items.length === 0) {
    return null
  }

  return (
    <section className="history-panel" aria-labelledby="history-heading">
      <div className="panel-heading">
        <div>
          <h2 id="history-heading">過去の判定</h2>
          <p>SQLite に保存された結果です。ブラウザを閉じても残ります。</p>
        </div>
        <span className="file-count">{items.length} 件</span>
      </div>
      <ul className="history-list">
        {items.map((item) => {
          const label = formatDate(item.created_at)
          return (
            <li key={item.id}>
              <button
                type="button"
                className="history-open"
                aria-label={`${label} の判定結果を開く`}
                onClick={() => onOpen(item.id)}
              >
                <span className="history-date">{label}</span>
                <span className="history-model">{item.model ?? '既定モデル'}</span>
                <span className="history-counts">
                  猫あり {item.cat_count} / 猫なし {item.no_cat_count} / エラー{' '}
                  {item.error_count}
                </span>
              </button>
              <button
                type="button"
                className="icon-button"
                aria-label={`${label} の履歴を削除`}
                onClick={() => onDelete(item.id)}
              >
                ×
              </button>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
