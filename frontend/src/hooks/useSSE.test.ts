import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_SETTINGS } from '../components/SettingsPanel'
import { buildRestoredPipelineState, useSSE } from './useSSE'

const originalFetch = globalThis.fetch
const { connectSSE, sendApproval } = vi.hoisted(() => ({
  connectSSE: vi.fn(async () => {}),
  sendApproval: vi.fn(async () => {}),
}))

vi.mock('../lib/sse-client', () => ({
  connectSSE,
  sendApproval,
}))

describe('buildRestoredPipelineState', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn()
    connectSSE.mockClear()
    sendApproval.mockClear()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('restores approval conversations with approval state and request', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'awaiting_approval',
        input: '沖縄の家族旅行を企画して',
        messages: [
          { event: 'text', data: { content: 'analysis', agent: 'data-search-agent' } },
          { event: 'text', data: { content: '# Plan', agent: 'marketing-plan-agent' } },
          {
            event: 'approval_request',
            data: {
              prompt: '確認してください',
              conversation_id: 'conv-approval',
              plan_markdown: '# Plan',
            },
          },
        ],
      },
      'conv-approval',
      DEFAULT_SETTINGS,
    )

    expect(state.status).toBe('approval')
    expect(state.agentProgress).toEqual({
      agent: 'approval',
      status: 'running',
      step: 3,
      total_steps: 5,
    })
    expect(state.approvalRequest).toEqual({
      prompt: '確認してください',
      conversation_id: 'conv-approval',
      plan_markdown: '# Plan',
      approval_scope: 'user',
      manager_email: undefined,
      manager_comment: undefined,
    })
    expect(state.currentVersion).toBe(0)
    expect(state.textContents).toHaveLength(2)
  })

  it('restores manager approval conversations with manager scope', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'awaiting_manager_approval',
        input: '沖縄の家族旅行を企画して',
        messages: [
          { event: 'text', data: { content: 'analysis', agent: 'data-search-agent' } },
          { event: 'text', data: { content: '# Revised Plan', agent: 'plan-revision-agent' } },
          {
            event: 'approval_request',
            data: {
              prompt: '修正版企画書を上司へ承認依頼しました。Teams の応答を待っています。',
              conversation_id: 'conv-manager-approval',
              plan_markdown: '# Revised Plan',
              approval_scope: 'manager',
              manager_email: 'manager@example.com',
              manager_approval_url: 'https://app.example.com/?manager_conversation_id=conv-manager-approval#manager_approval_token=token-123',
              manager_delivery_mode: 'manual',
            },
          },
        ],
      },
      'conv-manager-approval',
      DEFAULT_SETTINGS,
    )

    expect(state.status).toBe('approval')
    expect(state.approvalRequest).toEqual({
      prompt: '修正版企画書を上司へ承認依頼しました。Teams の応答を待っています。',
      conversation_id: 'conv-manager-approval',
      plan_markdown: '# Revised Plan',
      approval_scope: 'manager',
      manager_email: 'manager@example.com',
      manager_comment: undefined,
      manager_approval_url: 'https://app.example.com/?manager_conversation_id=conv-manager-approval#manager_approval_token=token-123',
      manager_delivery_mode: 'manual',
    })
    expect(state.managerApprovalPolling).toBe(true)
    expect(state.hasManagerApprovalPhase).toBe(true)
  })

  it('restores a second manager approval with the previous committed version still selectable', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'awaiting_manager_approval',
        input: '沖縄の家族旅行を企画して',
        messages: [
          { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-second-manager', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
          { event: 'text', data: { content: '# Plan v2', agent: 'marketing-plan-agent' } },
          {
            event: 'approval_request',
            data: {
              prompt: '修正版企画書を上司へ承認依頼しました。Teams の応答を待っています。',
              conversation_id: 'conv-second-manager',
              plan_markdown: '# Plan v2',
              approval_scope: 'manager',
              manager_email: 'manager@example.com',
            },
          },
        ],
      },
      'conv-second-manager',
      DEFAULT_SETTINGS,
    )

    expect(state.currentVersion).toBe(1)
    expect(state.versions).toHaveLength(1)
    expect(state.pendingVersion).toEqual({
      version: 2,
      textOffset: 1,
      imageOffset: 0,
      toolEventOffset: 0,
    })
    expect(state.approvalRequest?.approval_scope).toBe('manager')
  })

  it('restores running manager approval continuations as running pipeline state', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'running',
        input: '沖縄の家族旅行を企画して',
        messages: [
          { event: 'text', data: { content: 'analysis', agent: 'data-search-agent' } },
          { event: 'text', data: { content: '# Revised Plan', agent: 'plan-revision-agent' } },
          {
            event: 'approval_request',
            data: {
              prompt: '修正版企画書を上司へ承認依頼しました。Teams の応答を待っています。',
              conversation_id: 'conv-running-manager',
              plan_markdown: '# Revised Plan',
              approval_scope: 'manager',
              manager_email: 'manager@example.com',
              manager_approval_url: 'https://app.example.com/?manager_conversation_id=conv-running-manager#manager_approval_token=token-123',
              manager_delivery_mode: 'manual',
            },
          },
        ],
      },
      'conv-running-manager',
      DEFAULT_SETTINGS,
    )

    expect(state.status).toBe('running')
    expect(state.managerApprovalPolling).toBe(true)
    expect(state.hasManagerApprovalPhase).toBe(true)
    expect(state.approvalRequest).toBeNull()
    expect(state.agentProgress).toEqual({
      agent: 'brochure-gen-agent',
      status: 'running',
      step: 5,
      total_steps: 5,
    })
  })

  it('restores all saved user messages from metadata for later rounds', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'completed',
        input: '京都の秋プランを企画して',
        metadata: {
          user_messages: [
            '京都の秋プランを企画して',
            '評価結果をもとに、価格訴求を弱めて上質感を強めて',
          ],
        },
        messages: [
          { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-user-history', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
          { event: 'text', data: { content: 'plan v2', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-user-history', metrics: { latency_seconds: 12, tool_calls: 2, total_tokens: 150 } } },
        ],
      },
      'conv-user-history',
      DEFAULT_SETTINGS,
    )

    expect(state.userMessages).toEqual([
      '京都の秋プランを企画して',
      '評価結果をもとに、価格訴求を弱めて上質感を強めて',
    ])
  })

  it('rebuilds version snapshots from completed multi-round conversations', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'completed',
        input: '京都の秋プランを企画して',
        messages: [
          { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
          { event: 'tool_event', data: { tool: 'web_search', status: 'completed', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-complete', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
          {
            event: 'evaluation_result',
            data: {
              version: 1,
              round: 1,
              created_at: '2026-04-02T00:00:00+00:00',
              result: { builtin: { relevance: { score: 4, reason: 'good' } } },
            },
          },
          { event: 'text', data: { content: 'plan v2', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-complete', metrics: { latency_seconds: 12, tool_calls: 2, total_tokens: 180 } } },
        ],
      },
      'conv-complete',
      DEFAULT_SETTINGS,
    )

    expect(state.status).toBe('completed')
    expect(state.versions).toHaveLength(2)
    expect(state.currentVersion).toBe(2)
    expect(state.versions[0].textContents).toEqual([{ content: 'plan v1', agent: 'marketing-plan-agent', content_type: undefined }])
    expect(state.versions[0].toolEvents).toHaveLength(1)
    expect(state.versions[0].metrics?.tool_calls).toBe(1)
    expect(state.versions[0].evaluations).toHaveLength(1)
    expect(state.versions[0].evaluations[0].round).toBe(1)
    expect(state.versions[1].textContents).toHaveLength(2)
    expect(state.versions[1].metrics?.tool_calls).toBe(2)
    expect(state.versions[1].evaluations).toEqual([])
    expect(state.metrics?.total_tokens).toBe(180)
  })

  it('assigns tool events to the correct version while restoring multiple rounds', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'completed',
        input: '京都の秋プランを企画して',
        messages: [
          { event: 'tool_event', data: { tool: 'web_search', status: 'completed', agent: 'marketing-plan-agent' } },
          { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-tools', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
          { event: 'tool_event', data: { tool: 'web_search', status: 'completed', agent: 'marketing-plan-agent' } },
          { event: 'text', data: { content: 'plan v2', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-tools', metrics: { latency_seconds: 12, tool_calls: 1, total_tokens: 120 } } },
        ],
      },
      'conv-tools',
      DEFAULT_SETTINGS,
    )

    expect(state.toolEvents.map(event => event.version)).toEqual([1, 2])
  })

  it('keeps polling metadata and merges background updates into the latest completed version', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'completed',
        input: '京都の秋プランを企画して',
        metadata: {
          background_updates_pending: true,
        },
        messages: [
          { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-background', background_updates_pending: true, metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
          { event: 'text', data: { content: 'https://example.com/video.mp4', agent: 'video-gen-agent', content_type: 'video', background_update: true } },
        ],
      },
      'conv-background',
      DEFAULT_SETTINGS,
    )

    expect(state.status).toBe('completed')
    expect(state.backgroundUpdatesPending).toBe(true)
    expect(state.versions).toHaveLength(1)
    expect(state.versions[0].textContents).toEqual([
      { content: 'plan v1', agent: 'marketing-plan-agent', content_type: undefined },
      { content: 'https://example.com/video.mp4', agent: 'video-gen-agent', content_type: 'video' },
    ])
  })

  it('attaches evaluations recorded before the first done event to the committed version', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'completed',
        input: '京都の秋プランを企画して',
        messages: [
          { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
          {
            event: 'evaluation_result',
            data: {
              version: 1,
              round: 1,
              created_at: '2026-04-02T00:00:00+00:00',
              result: { builtin: { relevance: { score: 4, reason: 'good' } } },
            },
          },
          { event: 'done', data: { conversation_id: 'conv-queued-eval', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
        ],
      },
      'conv-queued-eval',
      DEFAULT_SETTINGS,
    )

    expect(state.versions).toHaveLength(1)
    expect(state.currentVersion).toBe(1)
    expect(state.versions[0].evaluations).toHaveLength(1)
    expect(state.versions[0].evaluations[0].round).toBe(1)
  })

  it('tracks a pending version while a refinement round is running', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'completed',
      input: '京都の秋プランを企画して',
      messages: [
        { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
        { event: 'done', data: { conversation_id: 'conv-complete', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
        { event: 'text', data: { content: 'plan v2', agent: 'marketing-plan-agent' } },
        { event: 'done', data: { conversation_id: 'conv-complete', metrics: { latency_seconds: 12, tool_calls: 2, total_tokens: 180 } } },
      ],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-complete')
    })

    expect(result.current.state.currentVersion).toBe(2)

    act(() => {
      void result.current.sendMessage('評価結果をもとに改善して')
    })

    await waitFor(() => {
      expect(result.current.state.pendingVersion).toEqual({
        version: 3,
        textOffset: 2,
        imageOffset: 0,
        toolEventOffset: 0,
      })
    })

    expect(result.current.state.status).toBe('running')
    expect(result.current.state.currentVersion).toBe(2)
    expect(connectSSE).toHaveBeenCalledTimes(1)

    act(() => {
      result.current.restoreVersion(1)
    })

    expect(result.current.state.currentVersion).toBe(2)
  })

  it('seeds a first snapshot when evaluating before the first round is committed', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'awaiting_approval',
      input: '沖縄の家族旅行を企画して',
      messages: [
        { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
        {
          event: 'approval_request',
          data: {
            prompt: '確認してください',
            conversation_id: 'conv-approval',
            plan_markdown: '# Plan v1',
          },
        },
      ],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-approval')
    })

    expect(result.current.state.currentVersion).toBe(0)
    expect(result.current.state.versions).toEqual([])

    act(() => {
      result.current.saveEvaluation({
        version: 1,
        round: 1,
        createdAt: '2026-04-02T00:00:00+00:00',
        result: { builtin: { relevance: { score: 4, reason: 'good' } } },
      })
    })

    expect(result.current.state.currentVersion).toBe(1)
    expect(result.current.state.versions).toHaveLength(1)
    expect(result.current.state.versions[0].evaluations).toHaveLength(1)

    act(() => {
      void result.current.sendMessage('評価結果をもとに改善して')
    })

    await waitFor(() => {
      expect(result.current.state.pendingVersion).toEqual({
        version: 2,
        textOffset: 1,
        imageOffset: 0,
        toolEventOffset: 0,
      })
    })
  })

  it('replaces the draft snapshot when the first evaluated run completes', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'awaiting_approval',
      input: '沖縄の家族旅行を企画して',
      messages: [
        { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
        {
          event: 'approval_request',
          data: {
            prompt: '確認してください',
            conversation_id: 'conv-eval-draft',
            plan_markdown: '# Plan v1',
          },
        },
      ],
    })))

    sendApproval.mockImplementationOnce(async (_threadId, _response, handlers) => {
      handlers.done({
        conversation_id: 'conv-eval-draft',
        metrics: { latency_seconds: 11, tool_calls: 1, total_tokens: 120 },
      })
    })

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-eval-draft')
    })

    act(() => {
      result.current.saveEvaluation({
        version: 1,
        round: 1,
        createdAt: '2026-04-02T00:00:00+00:00',
        result: { builtin: { relevance: { score: 4, reason: 'good' } } },
      })
    })

    expect(result.current.state.versions).toHaveLength(1)
    expect(result.current.state.versions[0].isDraft).toBe(true)

    await act(async () => {
      await result.current.approve('approve')
    })

    expect(result.current.state.status).toBe('completed')
    expect(result.current.state.currentVersion).toBe(1)
    expect(result.current.state.versions).toHaveLength(1)
    expect(result.current.state.versions[0].isDraft).toBe(false)
    expect(result.current.state.versions[0].evaluations).toHaveLength(1)
    expect(result.current.state.versions[0].metrics?.total_tokens).toBe(120)
  })

  it('keeps the first run as v1 after approval', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'awaiting_approval',
      input: '沖縄の家族旅行を企画して',
      messages: [
        { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
        {
          event: 'approval_request',
          data: {
            prompt: '確認してください',
            conversation_id: 'conv-first-run',
            plan_markdown: '# Plan v1',
          },
        },
      ],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-first-run')
    })

    act(() => {
      void result.current.approve('approve')
    })

    await waitFor(() => {
      expect(result.current.state.pendingVersion).toEqual({
        version: 1,
        textOffset: 0,
        imageOffset: 0,
        toolEventOffset: 0,
      })
    })

    expect(result.current.state.versions).toEqual([])
    expect(sendApproval).toHaveBeenCalledTimes(1)
  })

  it('infers v2 after approving a restored refinement draft', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'awaiting_approval',
      input: '京都の秋プランを企画して',
      messages: [
        { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
        { event: 'done', data: { conversation_id: 'conv-refine', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
        { event: 'text', data: { content: '# Plan v2', agent: 'marketing-plan-agent' } },
        {
          event: 'approval_request',
          data: {
            prompt: '改善案を確認してください',
            conversation_id: 'conv-refine',
            plan_markdown: '# Plan v2',
          },
        },
      ],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-refine')
    })

    act(() => {
      void result.current.approve('approve')
    })

    await waitFor(() => {
      expect(result.current.state.pendingVersion).toEqual({
        version: 2,
        textOffset: 1,
        imageOffset: 0,
        toolEventOffset: 0,
      })
    })

    expect(sendApproval).toHaveBeenCalledTimes(1)
  })

  it('appends approval revisions to local user message history immediately', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'awaiting_approval',
      input: '京都の秋プランを企画して',
      metadata: {
        user_messages: ['京都の秋プランを企画して'],
      },
      messages: [
        { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
        {
          event: 'approval_request',
          data: {
            prompt: '確認してください',
            conversation_id: 'conv-approval-revision',
            plan_markdown: '# Plan v1',
          },
        },
      ],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-approval-revision')
    })

    act(() => {
      void result.current.approve('価格訴求を少し控えめにしてください')
    })

    expect(result.current.state.userMessages).toEqual([
      '京都の秋プランを企画して',
      '価格訴求を少し控えめにしてください',
    ])
    expect(sendApproval).toHaveBeenCalledWith(
      'conv-approval-revision',
      '価格訴求を少し控えめにしてください',
      expect.any(Object),
      expect.any(AbortSignal),
    )
  })

  it('restores conversations with cache-busting fetch options', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'running',
      input: '沖縄の家族旅行を企画して',
      messages: [],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-cache-test')
    })

    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    const [url, options] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(String(url)).toContain('/api/conversations/conv-cache-test')
    expect(String(url)).toContain('ts=')
    expect(options).toMatchObject({
      cache: 'no-store',
      headers: {
        'Cache-Control': 'no-cache',
      },
    })
  })

  it('keeps refinement prompts after restore polling completes', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'completed',
      input: '京都の秋プランを企画して',
      metadata: {
        user_messages: [
          '京都の秋プランを企画して',
          '評価結果をもとに、価格訴求を弱めて上質感を強めて',
        ],
      },
      messages: [
        { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
        { event: 'done', data: { conversation_id: 'conv-restore-history', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
        { event: 'text', data: { content: 'plan v2', agent: 'marketing-plan-agent' } },
        { event: 'done', data: { conversation_id: 'conv-restore-history', metrics: { latency_seconds: 12, tool_calls: 2, total_tokens: 150 } } },
      ],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-restore-history')
    })

    expect(result.current.state.userMessages).toEqual([
      '京都の秋プランを企画して',
      '評価結果をもとに、価格訴求を弱めて上質感を強めて',
    ])
  })

  it('assigns version 1 to live tool events during the first run', async () => {
    connectSSE.mockImplementationOnce(async (_message, handlers) => {
      handlers.tool_event?.({
        tool: 'search_sales_history',
        status: 'completed',
        agent: 'data-search-agent',
      })
    })

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.sendMessage('沖縄プランを企画して')
    })

    expect(result.current.state.toolEvents).toEqual([
      {
        tool: 'search_sales_history',
        status: 'completed',
        agent: 'data-search-agent',
        version: 1,
      },
    ])
  })

  it('assigns the pending version number to live tool events during a refinement run', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'completed',
      input: '京都の秋プランを企画して',
      messages: [
        { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
        { event: 'done', data: { conversation_id: 'conv-live-tools', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
      ],
    })))

    connectSSE.mockImplementationOnce(async (_message, handlers) => {
      handlers.tool_event?.({
        tool: 'web_search',
        status: 'completed',
        agent: 'marketing-plan-agent',
      })
    })

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-live-tools')
    })

    await act(async () => {
      await result.current.sendMessage('評価結果をもとに改善して')
    })

    expect(result.current.state.pendingVersion).toEqual({
      version: 2,
      textOffset: 1,
      imageOffset: 0,
      toolEventOffset: 0,
    })
    expect(result.current.state.toolEvents.at(-1)).toEqual({
      tool: 'web_search',
      status: 'completed',
      agent: 'marketing-plan-agent',
      version: 2,
    })
  })

  it('keeps a locally evaluated draft after approval request and restore polling', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'awaiting_approval',
      input: '沖縄の家族旅行を企画して',
      messages: [
        { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
        {
          event: 'approval_request',
          data: {
            prompt: '確認してください',
            conversation_id: 'conv-draft-eval',
            plan_markdown: '# Plan v1',
          },
        },
      ],
    })))

    let releaseApprovalRequest: (() => void) | null = null
    connectSSE.mockImplementationOnce(async (_message, handlers) => {
      handlers.text?.({
        content: '# Plan v1',
        agent: 'marketing-plan-agent',
      })
      await new Promise<void>((resolve) => {
        releaseApprovalRequest = resolve
      })
      handlers.approval_request?.({
        prompt: '確認してください',
        conversation_id: 'conv-draft-eval',
        plan_markdown: '# Plan v1',
      })
    })

    const { result } = renderHook(() => useSSE())

    act(() => {
      void result.current.sendMessage('沖縄の家族旅行を企画して')
    })

    await waitFor(() => {
      expect(result.current.state.textContents).toHaveLength(1)
    })

    act(() => {
      result.current.saveEvaluation({
        version: 1,
        round: 1,
        createdAt: '2026-04-04T00:00:00+00:00',
        result: { builtin: { relevance: { score: 4, reason: 'good' } } },
      })
    })

    expect(result.current.state.versions).toHaveLength(1)
    expect(result.current.state.versions[0].evaluations).toHaveLength(1)

    await act(async () => {
      releaseApprovalRequest?.()
    })

    await waitFor(() => {
      expect(result.current.state.conversationId).toBe('conv-draft-eval')
      expect(result.current.state.status).toBe('approval')
    })

    await act(async () => {
      await result.current.restoreConversation('conv-draft-eval')
    })

    expect(result.current.state.versions).toHaveLength(1)
    expect(result.current.state.versions[0].evaluations).toHaveLength(1)
    expect(result.current.state.versions[0].evaluations[0].round).toBe(1)
  })
})
