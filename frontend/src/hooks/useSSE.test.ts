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
    })
    expect(state.currentVersion).toBe(0)
    expect(state.textContents).toHaveLength(2)
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
})
