import type { AnalysisSummary } from '../api'

export type ResultFilter = 'all' | 'cat' | 'no_cat' | 'error'

interface SummaryBarProps {
  summary: AnalysisSummary
  filter: ResultFilter
  onFilter: (filter: ResultFilter) => void
}

const FILTERS: Array<{ value: ResultFilter; label: string }> = [
  { value: 'all', label: 'すべて' },
  { value: 'cat', label: '猫あり' },
  { value: 'no_cat', label: '猫なし' },
  { value: 'error', label: 'エラー' },
]

export function SummaryBar({ summary, filter, onFilter }: SummaryBarProps) {
  const counts: Record<ResultFilter, number> = {
    all: summary.total,
    cat: summary.cat_count,
    no_cat: summary.no_cat_count,
    error: summary.error_count,
  }

  return (
    <section className="summary" aria-label="判定結果の集計">
      <div className="summary-counts">
        <div className="count cat">
          <strong>{summary.cat_count}</strong>
          <span>猫あり</span>
        </div>
        <div className="count no-cat">
          <strong>{summary.no_cat_count}</strong>
          <span>猫なし</span>
        </div>
        <div className="count error">
          <strong>{summary.error_count}</strong>
          <span>エラー</span>
        </div>
      </div>

      <div className="filters" role="group" aria-label="結果の絞り込み">
        {FILTERS.map((item) => (
          <button
            key={item.value}
            type="button"
            className={`filter${filter === item.value ? ' is-active' : ''}`}
            aria-pressed={filter === item.value}
            onClick={() => onFilter(item.value)}
          >
            {item.label} ({counts[item.value]})
          </button>
        ))}
      </div>
    </section>
  )
}
