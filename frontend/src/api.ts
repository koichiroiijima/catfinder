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

export interface ChatOptions {
  reasoning_effort: string | null
  temperature: number | null
  max_tokens: number | null
}

export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  reasoning_tokens: number | null
  cost: number | null
}

export interface ChatOptionInput {
  reasoning_effort: string
  temperature: string
  max_tokens: string
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
  options: ChatOptions
  usage: TokenUsage | null
  eta_seconds: number | null
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
  options: ChatOptionInput,
): Promise<AnalysisCreated> {
  const form = new FormData()
  for (const file of files) {
    form.append('files', file, file.name)
  }
  if (model !== null && model !== '') {
    form.append('model', model)
  }
  const reasoningEffort = options.reasoning_effort.trim()
  if (reasoningEffort !== '') {
    form.append('reasoning_effort', reasoningEffort)
  }
  const temperature = options.temperature.trim()
  if (temperature !== '') {
    form.append('temperature', temperature)
  }
  const maxTokens = options.max_tokens.trim()
  if (maxTokens !== '') {
    form.append('max_tokens', maxTokens)
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
