/**
 * SSE クライアントのテスト
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { connectSSE, type SSEHandlers } from '../sse-client'

const originalFetch = global.fetch

function createMockResponse(body: string, status = 200): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(body))
      controller.close()
    },
  })
  return new Response(stream, {
    status,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('connectSSE', () => {
  const mockFetch = vi.fn()

  beforeEach(() => {
    global.fetch = mockFetch
  })

  afterEach(() => {
    global.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('sends correct POST request with message body', async () => {
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))
    const handlers: SSEHandlers = {}

    await connectSSE('hello', handlers)

    expect(mockFetch).toHaveBeenCalledTimes(1)
    const [url, options] = mockFetch.mock.calls[0]
    expect(url).toBe('/api/chat')
    expect(options.method).toBe('POST')
    expect(options.headers['Content-Type']).toBe('application/json')
    const body = JSON.parse(options.body)
    expect(body.message).toBe('hello')
  })

  it('parses SSE events and dispatches to correct handlers', async () => {
    const sseBody =
      'event: agent_progress\ndata: {"agent":"a1","status":"running","step":1,"total_steps":2}\n\n' +
      'event: text\ndata: {"content":"hello","agent":"a1"}\n\n' +
      'event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'

    mockFetch.mockResolvedValue(createMockResponse(sseBody))

    const agentHandler = vi.fn()
    const textHandler = vi.fn()
    const doneHandler = vi.fn()
    const handlers: SSEHandlers = {
      agent_progress: agentHandler,
      text: textHandler,
      done: doneHandler,
    }

    await connectSSE('test', handlers)

    expect(agentHandler).toHaveBeenCalledWith({ agent: 'a1', status: 'running', step: 1, total_steps: 2 })
    expect(textHandler).toHaveBeenCalledWith({ content: 'hello', agent: 'a1' })
    expect(doneHandler).toHaveBeenCalledWith({ conversation_id: 'c1', metrics: {} })
  })

  it('handles partial lines across chunks via buffering', async () => {
    const encoder = new TextEncoder()
    const chunk1 = 'event: text\ndata: {"content":'
    const chunk2 = '"buffered","agent":"a1"}\n\n'

    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(chunk1))
        controller.enqueue(encoder.encode(chunk2))
        controller.close()
      },
    })
    mockFetch.mockResolvedValue(new Response(stream, { status: 200 }))

    const textHandler = vi.fn()
    await connectSSE('test', { text: textHandler })

    expect(textHandler).toHaveBeenCalledWith({ content: 'buffered', agent: 'a1' })
  })

  it('calls error handler on HTTP errors (non-2xx)', async () => {
    mockFetch.mockResolvedValue(new Response(null, { status: 500 }))

    const errorHandler = vi.fn()
    await connectSSE('test', { error: errorHandler })

    expect(errorHandler).toHaveBeenCalledWith({ message: 'HTTP 500', code: 'HTTP_ERROR' })
  })

  it('calls error handler when AbortSignal is triggered', async () => {
    const abortError = new DOMException('The operation was aborted.', 'AbortError')
    mockFetch.mockRejectedValue(abortError)

    const errorHandler = vi.fn()
    const controller = new AbortController()
    controller.abort()

    await connectSSE('test', { error: errorHandler }, undefined, controller.signal)

    expect(errorHandler).toHaveBeenCalledWith(
      expect.objectContaining({ code: 'ABORT' }),
    )
  })

  it('dispatches multiple event types to their respective handlers', async () => {
    const sseBody =
      'event: tool_event\ndata: {"tool":"web_search","status":"completed","agent":"a1"}\n\n' +
      'event: image\ndata: {"url":"http://img.png","alt":"test","agent":"a1"}\n\n'

    mockFetch.mockResolvedValue(createMockResponse(sseBody))

    const toolHandler = vi.fn()
    const imageHandler = vi.fn()

    await connectSSE('test', {
      tool_event: toolHandler,
      image: imageHandler,
    })

    expect(toolHandler).toHaveBeenCalledTimes(1)
    expect(imageHandler).toHaveBeenCalledTimes(1)
    expect(toolHandler).toHaveBeenCalledWith(expect.objectContaining({ tool: 'web_search' }))
    expect(imageHandler).toHaveBeenCalledWith(expect.objectContaining({ url: 'http://img.png' }))
  })
})
