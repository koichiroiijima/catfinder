import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const modelCatalog = { models: ['vision-x', 'vision-y'], default: 'vision-x' }

const completed = {
  id: 'job-1',
  status: 'completed',
  total: 2,
  completed: 2,
  cat_count: 1,
  no_cat_count: 1,
  error_count: 0,
  model: 'vision-x',
  options: {
    reasoning_effort: 'high',
    temperature: 0.2,
    max_tokens: 1000,
  },
  usage: {
    input_tokens: 11000,
    output_tokens: 1345,
    total_tokens: 12345,
    reasoning_tokens: 500,
    cost: 0.0012,
  },
  eta_seconds: null,
  images: [
    {
      id: 'image-1',
      filename: 'cat.png',
      status: 'cat',
      has_cat: true,
      confidence: 0.91,
      reason: '茶色の猫が写っています。',
      error: null,
      url: '/api/analyses/job-1/images/image-1',
    },
    {
      id: 'image-2',
      filename: 'dog.png',
      status: 'no_cat',
      has_cat: false,
      confidence: 0.83,
      reason: '猫は写っていません。',
      error: null,
      url: '/api/analyses/job-1/images/image-2',
    },
  ],
}

const running = {
  ...completed,
  status: 'running',
  completed: 1,
  cat_count: 1,
  no_cat_count: 0,
  eta_seconds: 125,
  images: [
    completed.images[0],
    {
      ...completed.images[1],
      status: 'running',
      has_cat: null,
      confidence: null,
      reason: null,
    },
  ],
}

function selectFiles(...names: string[]) {
  const input = screen.getByLabelText('画像を選択')
  const files = names.map((name) => new File([name], name, { type: 'image/png' }))
  fireEvent.change(input, { target: { files } })
}

function postedForm(fetchMock: ReturnType<typeof vi.fn>, index: number): FormData | undefined {
  const body = fetchMock.mock.calls[index]?.[1]?.body
  expect(body).toBeInstanceOf(FormData)
  return body as FormData
}

describe('App', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('uploads selected images and renders verdicts', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse(modelCatalog))
      .mockResolvedValueOnce(jsonResponse({ id: 'job-1', status: 'queued', total: 2 }, 202))
      .mockResolvedValueOnce(jsonResponse(completed))

    render(<App />)
    selectFiles('cat.png', 'dog.png')

    expect(await screen.findByText('cat.png')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    expect((await screen.findAllByText('猫あり')).length).toBeGreaterThan(0)
    expect(screen.getByText('茶色の猫が写っています。')).toBeInTheDocument()
    expect(screen.getByText('猫は写っていません。')).toBeInTheDocument()
    expect(screen.getByText('確信度 91%')).toBeInTheDocument()
    expect(screen.getByText('判定モデル: vision-x')).toBeInTheDocument()
    expect(
      screen.getByText('設定: 推論: 高 / temperature: 0.2 / 最大出力: 1,000'),
    ).toBeInTheDocument()
    expect(screen.getByText(/使用トークン: 12,345/)).toBeInTheDocument()
    expect(screen.getByText(/コスト: \$0\.001200/)).toBeInTheDocument()

    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/models')
    const postUrl = fetchMock.mock.calls[1]?.[0]
    expect(postUrl).toBe('/api/analyses')
    const body = postedForm(fetchMock, 1)
    expect(body?.get('model')).toBe('vision-x')
    expect(body?.has('reasoning_effort')).toBe(false)
    expect(body?.has('temperature')).toBe(false)
    expect(body?.has('max_tokens')).toBe(false)
  })

  it('caps the selection at 500 images', () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(modelCatalog))

    render(<App />)
    selectFiles(...Array.from({ length: 501 }, (_, index) => `cat-${index}.png`))

    expect(screen.getByText('一度に追加できるのは 500 枚までです。')).toBeInTheDocument()
    expect(screen.getByText('500 枚')).toBeInTheDocument()
  })

  it('shows a rough remaining time while running', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(modelCatalog))
      .mockResolvedValueOnce(jsonResponse({ id: 'job-1', status: 'queued', total: 2 }, 202))
      .mockResolvedValueOnce(jsonResponse(running))
      .mockResolvedValue(jsonResponse(running))

    render(<App />)
    selectFiles('cat.png', 'dog.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    expect(await screen.findByText('残り時間: 約 3 分（概算）')).toBeInTheDocument()
  })

  it('renders verdicts when the server response omits chat options', async () => {
    const withoutOptions = { ...completed, options: undefined }
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(modelCatalog))
      .mockResolvedValueOnce(jsonResponse({ id: 'job-1', status: 'queued', total: 2 }, 202))
      .mockResolvedValueOnce(jsonResponse(withoutOptions))

    render(<App />)
    selectFiles('cat.png', 'dog.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    expect(await screen.findByText('茶色の猫が写っています。')).toBeInTheDocument()
    expect(screen.queryByText(/設定:/)).not.toBeInTheDocument()
  })

  it('sends advanced chat options', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse(modelCatalog))
      .mockResolvedValueOnce(jsonResponse({ id: 'job-1', status: 'queued', total: 1 }, 202))
      .mockResolvedValueOnce(jsonResponse(completed))

    render(<App />)
    fireEvent.change(screen.getByLabelText('推論の強さ'), { target: { value: 'high' } })
    fireEvent.change(screen.getByLabelText('Temperature'), { target: { value: '0.2' } })
    fireEvent.change(screen.getByLabelText('最大出力トークン'), { target: { value: '500' } })
    selectFiles('cat.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    await screen.findByText('茶色の猫が写っています。')
    const body = postedForm(fetchMock, 1)
    expect(body?.get('reasoning_effort')).toBe('high')
    expect(body?.get('temperature')).toBe('0.2')
    expect(body?.get('max_tokens')).toBe('500')
  })

  it('sends the model chosen in the select', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockResolvedValueOnce(jsonResponse(modelCatalog))
      .mockResolvedValueOnce(jsonResponse({ id: 'job-1', status: 'queued', total: 2 }, 202))
      .mockResolvedValueOnce(jsonResponse(completed))

    render(<App />)
    fireEvent.change(await screen.findByLabelText('判定モデル'), {
      target: { value: 'vision-y' },
    })
    selectFiles('cat.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    await screen.findByText('茶色の猫が写っています。')
    expect(postedForm(fetchMock, 1)?.get('model')).toBe('vision-y')
    expect(screen.getByText('判定モデル: vision-x')).toBeInTheDocument()
  })

  it('hides the model select when the catalog is unavailable', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(jsonResponse({ id: 'job-1', status: 'queued', total: 1 }, 202))
      .mockResolvedValueOnce(jsonResponse(completed))

    render(<App />)
    selectFiles('cat.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    await screen.findByText('茶色の猫が写っています。')
    expect(screen.queryByLabelText('判定モデル')).not.toBeInTheDocument()
    expect(postedForm(fetchMock, 1)?.has('model')).toBe(false)
  })

  it('shows the server error and keeps the upload panel', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(modelCatalog))
      .mockResolvedValueOnce(
        jsonResponse({ detail: 'LLM が設定されていません。LLM_MODEL を設定してください。' }, 503),
      )

    render(<App />)
    selectFiles('cat.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('LLM が設定されていません')
    expect(screen.getByRole('button', { name: '判定する' })).toBeInTheDocument()
  })

  it('shows a Japanese message when the upload request fails', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(modelCatalog))
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))

    render(<App />)
    selectFiles('cat.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('通信に失敗しました。')
  })

  it('filters results by status', async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(modelCatalog))
      .mockResolvedValueOnce(jsonResponse({ id: 'job-1', status: 'queued', total: 2 }, 202))
      .mockResolvedValueOnce(jsonResponse(completed))

    render(<App />)
    selectFiles('cat.png', 'dog.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    await screen.findByText('茶色の猫が写っています。')
    fireEvent.click(screen.getByRole('button', { name: '猫なし (1)' }))

    expect(screen.getByAltText('dog.png')).toBeInTheDocument()
    expect(screen.queryByAltText('cat.png')).not.toBeInTheDocument()
  })

  it('ignores the model catalog failure without surfacing an error', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(modelCatalog))

    render(<App />)
    expect(await screen.findByLabelText('判定モデル')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
