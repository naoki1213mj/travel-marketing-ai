/**
 * SSE クライアントのテスト
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_CONVERSATION_SETTINGS } from '../../components/SettingsPanel'
import { connectSSE, sendApproval, type SSEHandlers } from '../sse-client'

const originalFetch = global.fetch
const { getDelegatedApiAuth, getDelegatedApiHeaders } = vi.hoisted(() => ({
  getDelegatedApiAuth: vi.fn(async () => ({ headers: {}, status: 'ok' })),
  getDelegatedApiHeaders: vi.fn(async () => ({})),
}))

vi.mock('../api-auth', () => ({
  getDelegatedApiAuth,
  getDelegatedApiHeaders,
}))

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
    mockFetch.mockReset()
    getDelegatedApiAuth.mockReset()
    getDelegatedApiAuth.mockResolvedValue({ headers: {}, status: 'ok' })
    getDelegatedApiHeaders.mockReset()
    getDelegatedApiHeaders.mockResolvedValue({})
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

  it('includes marketing plan runtime in workflow settings', async () => {
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))

    await connectSSE(
      'hello',
      {},
      undefined,
      undefined,
      {
        model: 'gpt-5.4-mini',
        temperature: 0.7,
        maxTokens: 2000,
        topP: 1,
        imageModel: 'gpt-image-1.5',
        imageQuality: 'medium',
        imageWidth: 1024,
        imageHeight: 1024,
        managerApprovalEnabled: false,
        managerEmail: '',
        iqSearchResults: 5,
        iqScoreThreshold: 0.3,
        marketingPlanRuntime: 'foundry_preprovisioned',
      },
    )

    const [, options] = mockFetch.mock.calls[0]
    const body = JSON.parse(options.body)
    expect(body.workflow_settings).toMatchObject({
      marketing_plan_runtime: 'foundry_preprovisioned',
    })
  })

  it('includes work iq runtime in workflow settings when present', async () => {
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))

    await connectSSE(
      'hello',
      {},
      undefined,
      undefined,
      {
        model: 'gpt-5.4-mini',
        temperature: 0.7,
        maxTokens: 2000,
        topP: 1,
        imageModel: 'gpt-image-1.5',
        imageQuality: 'medium',
        imageWidth: 1024,
        imageHeight: 1024,
        managerApprovalEnabled: false,
        managerEmail: '',
        iqSearchResults: 5,
        iqScoreThreshold: 0.3,
        marketingPlanRuntime: 'foundry_preprovisioned',
        workIqRuntime: 'foundry_tool',
      },
    )

    const [, options] = mockFetch.mock.calls[0]
    const body = JSON.parse(options.body)
    expect(body.workflow_settings).toMatchObject({
      work_iq_runtime: 'foundry_tool',
    })
  })

  it('sends conversation settings when starting a new conversation', async () => {
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))

    await connectSSE('hello', {}, undefined, undefined, undefined, {
      workIqEnabled: true,
      workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
    })

    const [, options] = mockFetch.mock.calls[0]
    const body = JSON.parse(options.body)
    expect(body.conversation_settings).toEqual({
      work_iq_enabled: true,
      source_scope: DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope,
      work_iq_source_scope: DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope,
    })
  })

  it('adds delegated auth headers for foundry Work IQ runtime', async () => {
    getDelegatedApiAuth.mockResolvedValue({
      headers: {
        Authorization: 'Bearer foundry-token',
        'X-Work-IQ-Graph-Authorization': 'Bearer graph-token',
      },
      status: 'ok',
    })
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))

    await connectSSE('hello', {}, undefined, undefined, undefined, {
      workIqEnabled: true,
      workIqSourceScope: ['emails'],
    })

    expect(getDelegatedApiAuth).toHaveBeenCalledWith({ interactive: true, workIqRuntime: 'foundry_tool' })
    const [, options] = mockFetch.mock.calls[0]
    expect(options.headers.Authorization).toBe('Bearer foundry-token')
    expect(options.headers['X-Work-IQ-Graph-Authorization']).toBe('Bearer graph-token')
    expect(options.headers['X-User-Timezone']).toBeTruthy()
  })

  it('passes graph_prefetch runtime to delegated auth lookup when present', async () => {
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))

    await connectSSE(
      'hello',
      {},
      undefined,
      undefined,
      {
        model: 'gpt-5.4-mini',
        temperature: 0.7,
        maxTokens: 2000,
        topP: 1,
        imageModel: 'gpt-image-1.5',
        imageQuality: 'medium',
        imageWidth: 1024,
        imageHeight: 1024,
        managerApprovalEnabled: false,
        managerEmail: '',
        iqSearchResults: 5,
        iqScoreThreshold: 0.3,
        marketingPlanRuntime: 'foundry_preprovisioned',
        workIqRuntime: 'graph_prefetch',
      },
      {
        workIqEnabled: true,
        workIqSourceScope: ['emails'],
      },
    )

    expect(getDelegatedApiAuth).toHaveBeenCalledWith({ interactive: true, workIqRuntime: 'graph_prefetch' })
  })

  it('keeps interactive auth enabled for Work IQ in existing conversations', async () => {
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"conv-1","metrics":{}}\n\n'))

    await connectSSE(
      'hello again',
      {},
      'conv-1',
      undefined,
      undefined,
      {
        workIqEnabled: true,
        workIqSourceScope: ['emails'],
      },
    )

    expect(getDelegatedApiAuth).toHaveBeenCalledWith({ interactive: true, workIqRuntime: 'foundry_tool' })
  })

  it('sends the selected image model in image settings', async () => {
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))

    await connectSSE(
      'hello',
      {},
      undefined,
      undefined,
      {
        model: 'gpt-5.4-mini',
        temperature: 0.7,
        maxTokens: 2000,
        topP: 1,
        imageModel: 'gpt-image-2',
        imageQuality: 'high',
        imageWidth: 1024,
        imageHeight: 1024,
        managerApprovalEnabled: false,
        managerEmail: '',
        iqSearchResults: 5,
        iqScoreThreshold: 0.3,
        marketingPlanRuntime: 'foundry_preprovisioned',
      },
    )

    const [, options] = mockFetch.mock.calls[0]
    const body = JSON.parse(options.body)
    expect(body.settings.image_settings).toMatchObject({
      image_model: 'gpt-image-2',
      image_quality: 'high',
    })
  })

  it('does not emit a synthetic Foundry Work IQ tool event before the request starts', async () => {
    const toolHandler = vi.fn()
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))

    await connectSSE('hello', { tool_event: toolHandler }, undefined, undefined, undefined, {
      workIqEnabled: true,
      workIqSourceScope: ['emails'],
    })

    expect(toolHandler).not.toHaveBeenCalled()
  })

  it('does not send the request after starting interactive redirect for graph prefetch', async () => {
    getDelegatedApiAuth.mockResolvedValue({ headers: {}, status: 'redirecting' })

    await connectSSE(
      'hello',
      {},
      undefined,
      undefined,
      {
        model: 'gpt-5.4-mini',
        temperature: 0.7,
        maxTokens: 2000,
        topP: 1,
        imageModel: 'gpt-image-1.5',
        imageQuality: 'medium',
        imageWidth: 1024,
        imageHeight: 1024,
        managerApprovalEnabled: false,
        managerEmail: '',
        iqSearchResults: 5,
        iqScoreThreshold: 0.3,
        marketingPlanRuntime: 'foundry_preprovisioned',
        workIqRuntime: 'graph_prefetch',
      },
      {
        workIqEnabled: true,
        workIqSourceScope: ['emails'],
      },
    )

    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('does not send the request after starting interactive redirect for foundry tool', async () => {
    getDelegatedApiAuth.mockResolvedValue({ headers: {}, status: 'redirecting' })

    await connectSSE(
      'hello',
      {},
      undefined,
      undefined,
      {
        model: 'gpt-5.4-mini',
        temperature: 0.7,
        maxTokens: 2000,
        topP: 1,
        imageModel: 'gpt-image-1.5',
        imageQuality: 'medium',
        imageWidth: 1024,
        imageHeight: 1024,
        managerApprovalEnabled: false,
        managerEmail: '',
        iqSearchResults: 5,
        iqScoreThreshold: 0.3,
        marketingPlanRuntime: 'foundry_preprovisioned',
        workIqRuntime: 'foundry_tool',
      },
      {
        workIqEnabled: true,
        workIqSourceScope: ['emails'],
      },
    )

    expect(mockFetch).not.toHaveBeenCalled()
  })

  it('omits conversation settings when continuing an existing conversation', async () => {
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))

    await connectSSE('hello', {}, 'conv-1', undefined, undefined, {
      workIqEnabled: true,
      workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
    })

    const [, options] = mockFetch.mock.calls[0]
    const body = JSON.parse(options.body)
    expect(body.conversation_settings).toBeUndefined()
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

  it('parses CRLF-delimited SSE events', async () => {
    const sseBody =
      'event: text\r\ndata: {"content":"crlf","agent":"a1"}\r\n\r\n' +
      'event: done\r\ndata: {"conversation_id":"c1","metrics":{}}\r\n\r\n'

    mockFetch.mockResolvedValue(createMockResponse(sseBody))

    const textHandler = vi.fn()
    const doneHandler = vi.fn()

    await connectSSE('test', {
      text: textHandler,
      done: doneHandler,
    })

    expect(textHandler).toHaveBeenCalledWith({ content: 'crlf', agent: 'a1' })
    expect(doneHandler).toHaveBeenCalledWith({ conversation_id: 'c1', metrics: {} })
  })

  it('joins multi-line data fields before parsing JSON', async () => {
    const sseBody =
      'event: text\n' +
      'data: {"content":"multiline",\n' +
      'data: "agent":"a1"}\n\n'

    mockFetch.mockResolvedValue(createMockResponse(sseBody))

    const textHandler = vi.fn()
    await connectSSE('test', { text: textHandler })

    expect(textHandler).toHaveBeenCalledWith({ content: 'multiline', agent: 'a1' })
  })

  it('reports malformed SSE payloads without throwing', async () => {
    const sseBody =
      'event: text\ndata: {"content":"broken"\n\n' +
      'event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'

    mockFetch.mockResolvedValue(createMockResponse(sseBody))

    const errorHandler = vi.fn()
    const doneHandler = vi.fn()

    await expect(connectSSE('test', { error: errorHandler, done: doneHandler })).resolves.toBe('started')

    expect(errorHandler).toHaveBeenCalledWith({
      message: 'SSE イベントの解析に失敗しました',
      code: 'INVALID_SSE_EVENT',
    })
    expect(doneHandler).toHaveBeenCalledWith({ conversation_id: 'c1', metrics: {} })
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

  it('adds delegated auth headers to approval requests when enabled', async () => {
    getDelegatedApiHeaders.mockResolvedValue({ Authorization: 'Bearer delegated-token' })
    mockFetch.mockResolvedValue(createMockResponse('event: done\ndata: {"conversation_id":"c1","metrics":{}}\n\n'))

    await sendApproval('conv-1', '承認', {}, undefined, true)

    const [, options] = mockFetch.mock.calls[0]
    expect(options.headers.Authorization).toBe('Bearer delegated-token')
  })
})
