import { useCallback, useEffect, useState } from 'react'

import {
  createAnalysis,
  getAnalysis,
  type AnalysisSummary,
} from './api'

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === 'TypeError') {
      return '通信に失敗しました。'
    }
    return error.message
  }
  return '通信に失敗しました。'
}

export interface AnalysisController {
  jobId: string | null
  summary: AnalysisSummary | null
  error: string | null
  starting: boolean
  start: (files: File[], model: string | null) => Promise<void>
  reset: () => void
}

export function useAnalysis(): AnalysisController {
  const [jobId, setJobId] = useState<string | null>(null)
  const [summary, setSummary] = useState<AnalysisSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  const start = useCallback(async (files: File[], model: string | null) => {
    setStarting(true)
    setError(null)
    setSummary(null)
    try {
      const created = await createAnalysis(files, model)
      setJobId(created.id)
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setStarting(false)
    }
  }, [])

  const reset = useCallback(() => {
    setJobId(null)
    setSummary(null)
    setError(null)
  }, [])

  useEffect(() => {
    if (jobId === null) {
      return
    }

    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const controller = new AbortController()

    const poll = async () => {
      try {
        const data = await getAnalysis(jobId, controller.signal)
        if (cancelled) {
          return
        }
        setSummary(data)
        if (data.status !== 'completed') {
          timer = setTimeout(poll, 1000)
        }
      } catch (caught) {
        if (cancelled || controller.signal.aborted) {
          return
        }
        setError(errorMessage(caught))
      }
    }

    timer = setTimeout(poll, 350)

    return () => {
      cancelled = true
      controller.abort()
      if (timer !== undefined) {
        clearTimeout(timer)
      }
    }
  }, [jobId])

  return { jobId, summary, error, starting, start, reset }
}
