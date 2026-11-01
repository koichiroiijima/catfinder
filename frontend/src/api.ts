export type ImageStatus = 'pending' | 'running' | 'cat' | 'no_cat' | 'error'
export type AnalysisStatus = 'queued' | 'running' | 'completed'

export interface ImageResult {
  id: string
  filename: string
  status: ImageStatus
  has_cat: boolean | null
  confidence: number | null
  reason: string | null
  error: string | null
  url: string
}

export interface AnalysisSummary {
  id: string
  status: AnalysisStatus
  total: number
  completed: number
  cat_count: number
  no_cat_count: number
  error_count: number
  model: string | null
  images: ImageResult[]
}

export interface AnalysisCreated {
  id: string
  status: AnalysisStatus
  total: number
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export interface ModelsResponse {
  models: string[]
  default: string
}

export async function fetchModels(signal?: AbortSignal): Promise<ModelsResponse> {
  const response = await fetch('/api/models', { signal })
  if (!response.ok) {
    throw new ApiError(await errorDetail(response), response.status)
  }
  return (await response.json()) as ModelsResponse
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: unknown }
    if (typeof payload.detail === 'string') {
      return payload.detail
    }
  } catch {
    // Fall through to the generic message.
  }
  return `サーバーでエラーが発生しました（${response.status}）。`
}

export async function createAnalysis(
  files: File[],
  model: string | null,
): Promise<AnalysisCreated> {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file, file.name)
  }
  if (model !== null && model !== '') {
    form.append('model', model)
  }

  const response = await fetch('/api/analyses', {
    method: 'POST',
    body: form,
  })
  if (!response.ok) {
    throw new ApiError(await errorDetail(response), response.status)
  }
  return (await response.json()) as AnalysisCreated
}

export async function getAnalysis(
  id: string,
  signal?: AbortSignal,
): Promise<AnalysisSummary> {
  const response = await fetch(`/api/analyses/${encodeURIComponent(id)}`, { signal })
  if (!response.ok) {
    throw new ApiError(await errorDetail(response), response.status)
  }
  return (await response.json()) as AnalysisSummary
}

export async function deleteAnalysis(id: string): Promise<void> {
  const response = await fetch(`/api/analyses/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
  if (!response.ok && response.status !== 404) {
    throw new ApiError(await errorDetail(response), response.status)
  }
}
