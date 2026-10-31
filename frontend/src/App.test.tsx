import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from './App'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

const completed = {
  id: 'job-1',
  status: 'completed',
  total: 2,
  completed: 2,
  cat_count: 1,
  no_cat_count: 1,
  error_count: 0,
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

function selectFiles(...names: string[]) {
  const input = screen.getByLabelText('画像を選択')
  const files = names.map((name) => new File([name], name, { type: 'image/png' }))
  fireEvent.change(input, { target: { files } })
}

describe('App', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('uploads selected images and renders verdicts', async () => {
    const fetchMock = vi.mocked(fetch)
    fetchMock
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

    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/analyses')
    expect(options).toMatchObject({ method: 'POST' })
    expect(options?.body).toBeInstanceOf(FormData)
  })

  it('shows the server error and keeps the upload panel', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ detail: 'LLM が設定されていません。LLM_MODEL を設定してください。' }, 503),
    )

    render(<App />)
    selectFiles('cat.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('LLM が設定されていません')
    expect(screen.getByRole('button', { name: '判定する' })).toBeInTheDocument()
  })

  it('shows a Japanese message when the network request fails', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Failed to fetch'))

    render(<App />)
    selectFiles('cat.png')
    fireEvent.click(screen.getByRole('button', { name: '判定する' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('通信に失敗しました。')
  })

  it('filters results by status', async () => {
    vi.mocked(fetch)
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
})
