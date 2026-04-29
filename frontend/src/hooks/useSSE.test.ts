import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_CONVERSATION_SETTINGS, DEFAULT_SETTINGS } from '../components/SettingsPanel'
import { recordMsalRedirectFailureSentinel } from '../lib/msal-redirect-sentinel'
import { buildRestoredPipelineState, useSSE } from './useSSE'

const originalFetch = globalThis.fetch
const { connectSSE, sendApproval } = vi.hoisted(() => ({
  connectSSE: vi.fn(async () => {}),
  sendApproval: vi.fn(async () => 'started'),
}))
const { getDelegatedApiAuth } = vi.hoisted(() => ({
  getDelegatedApiAuth: vi.fn(async () => ({ headers: {}, status: 'ok' })),
}))

vi.mock('../lib/sse-client', () => ({
  connectSSE,
  sendApproval,
}))

vi.mock('../lib/api-auth', () => ({
  getDelegatedApiAuth,
}))

describe('buildRestoredPipelineState', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn()
    connectSSE.mockClear()
    sendApproval.mockClear()
    getDelegatedApiAuth.mockClear()
    getDelegatedApiAuth.mockResolvedValue({ headers: {}, status: 'ok' })
    window.sessionStorage.clear()
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

  it('restores optional evidence, chart, trace, and debug data from image events', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'completed',
        input: '沖縄の家族旅行を企画して',
        messages: [
          {
            event: 'image',
            data: {
              url: 'data:image/png;base64,abc123',
              alt: 'hero image',
              agent: 'brochure-gen-agent',
              evidence: [{ source: 'image-model', title: 'Prompt policy' }],
              charts: [{ chart_type: 'kpi', title: 'Image latency', data: [{ label: 'ms', value: 120 }] }],
              trace_events: [{ name: 'image.generate', status: 'completed', duration_ms: 120 }],
              debug_events: [{ level: 'info', message: 'placeholder not used' }],
            },
          },
          { event: 'done', data: { conversation_id: 'conv-images', metrics: { latency_seconds: 1, tool_calls: 1, total_tokens: 0 } } },
        ],
      },
      'conv-images',
      DEFAULT_SETTINGS,
    )

    expect(state.images[0]).toMatchObject({
      alt: 'hero image',
      agent: 'brochure-gen-agent',
      evidence: [{ source: 'image-model', title: 'Prompt policy' }],
      charts: [{ chart_type: 'kpi', title: 'Image latency', data: [{ label: 'ms', value: 120 }] }],
      trace_events: [{ name: 'image.generate', status: 'completed', duration_ms: 120 }],
      debug_events: [{ level: 'info', message: 'placeholder not used' }],
    })
    expect(state.versions[0].images[0].charts?.[0].title).toBe('Image latency')
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

  it('restores locked Work IQ state from conversation metadata', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'completed',
        metadata: {
          work_iq_session: {
            enabled: true,
            status: 'consent_required',
            source_scope: ['emails', 'teams_chats'],
            brief_summary: '<b>安全な要約</b>',
            brief_source_metadata: [
              { source: 'emails', count: 2, status: 'completed', summary: '<b>メール要約</b>' },
            ],
          },
        },
        messages: [],
      },
      'conv-workiq',
      DEFAULT_SETTINGS,
    )

    expect(state.conversationSettings).toEqual({
      workIqEnabled: true,
      workIqSourceScope: ['emails', 'teams_chats'],
    })
    expect(state.workIq.status).toBe('consent_required')
    expect(state.workIq.briefSummary).toBe('安全な要約')
    expect(state.workIq.sourceMetadata).toEqual([
      { source: 'emails', count: 2, status: 'completed', summary: 'メール要約' },
    ])
  })

  it('restores Work IQ warning_code when status is absent', () => {
    const state = buildRestoredPipelineState(
      {
        status: 'completed',
        metadata: {
          work_iq_session: {
            enabled: true,
            warning_code: 'auth_required',
            source_scope: ['emails'],
          },
        },
        messages: [],
      },
      'conv-workiq-warning',
      DEFAULT_SETTINGS,
    )

    expect(state.workIq.status).toBe('sign_in_required')
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

  it('continues a refinement run on the completed conversation thread', async () => {
    connectSSE
      .mockImplementationOnce(async (_message, handlers) => {
        handlers.text?.({ content: 'plan v1', agent: 'marketing-plan-agent' })
        handlers.done?.({
          conversation_id: 'conv-v1',
          metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 },
        })
      })
      .mockImplementationOnce(async () => {})

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.sendMessage('京都の秋プランを企画して')
    })

    expect(result.current.state.conversationId).toBe('conv-v1')
    expect(connectSSE).toHaveBeenNthCalledWith(
      1,
      '京都の秋プランを企画して',
      expect.any(Object),
      undefined,
      expect.any(AbortSignal),
      DEFAULT_SETTINGS,
      DEFAULT_CONVERSATION_SETTINGS,
      undefined,
    )

    await act(async () => {
      await result.current.sendMessage('評価結果をもとに改善して')
    })

    expect(connectSSE).toHaveBeenNthCalledWith(
      2,
      '評価結果をもとに改善して',
      expect.any(Object),
      'conv-v1',
      expect.any(AbortSignal),
      DEFAULT_SETTINGS,
      DEFAULT_CONVERSATION_SETTINGS,
      undefined,
    )
  })

  it('passes refine context when an evaluation-based refinement starts', async () => {
    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.sendMessage('評価結果をもとに改善して', {
        refineContext: {
          source: 'evaluation',
          artifactVersion: 2,
        },
      })
    })

    expect(connectSSE).toHaveBeenNthCalledWith(
      1,
      '評価結果をもとに改善して',
      expect.any(Object),
      undefined,
      expect.any(AbortSignal),
      DEFAULT_SETTINGS,
      DEFAULT_CONVERSATION_SETTINGS,
      {
        refineContext: {
          source: 'evaluation',
          artifactVersion: 2,
        },
      },
    )
  })

  it('restores the previous state when auth redirects before the SSE request starts', async () => {
    connectSSE.mockResolvedValueOnce('redirecting')

    const { result } = renderHook(() => useSSE())

    expect(result.current.state.status).toBe('idle')
    expect(result.current.state.userMessages).toEqual([])

    await act(async () => {
      await result.current.sendMessage('沖縄プランを企画して')
    })

    expect(result.current.state.status).toBe('idle')
    expect(result.current.state.pendingVersion).toBeNull()
    expect(result.current.state.userMessages).toEqual([])
  })

  it('persists and resumes a pending Work IQ request across the redirect round-trip', async () => {
    connectSSE
      .mockResolvedValueOnce('redirecting')
      .mockImplementationOnce(async (_message, handlers) => {
        handlers.agent_progress?.({
          agent: 'marketing-plan-agent',
          status: 'running',
          step: 2,
          total_steps: 5,
        })
        handlers.tool_event?.({
          tool: 'workiq_foundry_tool',
          status: 'completed',
          agent: 'marketing-plan-agent',
          provider: 'foundry',
          source: 'workiq',
          source_scope: ['emails'],
        })
        handlers.text?.({ content: '# Okinawa plan', agent: 'marketing-plan-agent' })
        handlers.done?.({
          conversation_id: 'conv-resumed',
          metrics: { latency_seconds: 12, tool_calls: 1, total_tokens: 100 },
        })
        return 'started'
      })

    const initialHook = renderHook(() => useSSE())

    act(() => {
      initialHook.result.current.updateConversationSettings({
        workIqEnabled: true,
        workIqSourceScope: ['emails'],
      })
    })

    await act(async () => {
      await initialHook.result.current.sendMessage('沖縄プランを企画して')
    })

    expect(initialHook.result.current.state.status).toBe('idle')
    expect(window.sessionStorage.getItem('workIqPendingChatRequest')).toContain('沖縄プランを企画して')

    initialHook.unmount()

    const resumedHook = renderHook(() => useSSE())

    await waitFor(() => {
      expect(connectSSE).toHaveBeenNthCalledWith(
        2,
        '沖縄プランを企画して',
        expect.any(Object),
        undefined,
        expect.any(AbortSignal),
        expect.objectContaining({ workIqRuntime: 'foundry_tool' }),
        { workIqEnabled: true, workIqSourceScope: ['emails'] },
        expect.not.objectContaining({ authInteractionMode: 'silent' }),
      )
    })

    await waitFor(() => {
      expect(resumedHook.result.current.state.status).toBe('completed')
    })

    expect(resumedHook.result.current.state.conversationId).toBe('conv-resumed')
    expect(resumedHook.result.current.state.userMessages).toEqual(['沖縄プランを企画して'])
    expect(resumedHook.result.current.state.workIq.status).toBe('enabled')
    expect(resumedHook.result.current.state.textContents).toContainEqual({
      content: '# Okinawa plan',
      agent: 'marketing-plan-agent',
    })
    expect(window.sessionStorage.getItem('workIqPendingChatRequest')).toBeNull()
  })

  it('stops auto-resume and surfaces an error when the redirect bridge recorded a failure', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    connectSSE.mockResolvedValueOnce('redirecting')

    const initialHook = renderHook(() => useSSE())

    act(() => {
      initialHook.result.current.updateConversationSettings({
        workIqEnabled: true,
        workIqSourceScope: ['emails'],
      })
    })

    await act(async () => {
      await initialHook.result.current.sendMessage('沖縄プランを企画して')
    })

    recordMsalRedirectFailureSentinel('redirect_bridge', new Error('bridge failed'))
    initialHook.unmount()

    const resumedHook = renderHook(() => useSSE())

    await waitFor(() => {
      expect(resumedHook.result.current.state.status).toBe('error')
    })

    expect(connectSSE).toHaveBeenCalledTimes(1)
    expect(resumedHook.result.current.state.error).toEqual(expect.objectContaining({
      code: 'WORKIQ_REDIRECT_FAILED',
    }))
    expect(resumedHook.result.current.state.workIq.status).toBe('unavailable')
    expect(resumedHook.result.current.state.conversationSettings).toEqual({
      workIqEnabled: true,
      workIqSourceScope: ['emails'],
    })
    expect(window.sessionStorage.getItem('workIqPendingChatRequest')).toBeNull()

    warnSpy.mockRestore()
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
      false,
    )
  })

  it('keeps delegated auth on approval for restored Work IQ foundry conversations', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'awaiting_approval',
      input: '京都の秋プランを企画して',
      metadata: {
        work_iq_session: {
          enabled: true,
          status: 'completed',
          source_scope: ['emails'],
        },
      },
      messages: [
        { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
        {
          event: 'approval_request',
          data: {
            prompt: '確認してください',
            conversation_id: 'conv-workiq-approval',
            plan_markdown: '# Plan v1',
          },
        },
      ],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-workiq-approval')
    })

    act(() => {
      void result.current.approve('approve')
    })

    expect(sendApproval).toHaveBeenCalledWith(
      'conv-workiq-approval',
      'approve',
      expect.any(Object),
      expect.any(AbortSignal),
      true,
    )
  })

  it('restores approval state when Work IQ approval needs an interactive auth redirect', async () => {
    sendApproval.mockResolvedValueOnce('redirecting')
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'awaiting_approval',
      input: '京都の秋プランを企画して',
      metadata: {
        work_iq_session: {
          enabled: true,
          status: 'completed',
          source_scope: ['emails'],
        },
      },
      messages: [
        { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
        {
          event: 'approval_request',
          data: {
            prompt: '確認してください',
            conversation_id: 'conv-workiq-redirect',
            plan_markdown: '# Plan v1',
          },
        },
      ],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-workiq-redirect')
    })

    await act(async () => {
      await result.current.approve('approve')
    })

    expect(result.current.state.status).toBe('approval')
    expect(result.current.state.approvalRequest?.conversation_id).toBe('conv-workiq-redirect')
  })

  it('resumes a pending Work IQ approval after the auth redirect round-trip', async () => {
    const approvalDocument = {
      status: 'awaiting_approval',
      input: '京都の秋プランを企画して',
      metadata: {
        work_iq_session: {
          enabled: true,
          status: 'completed',
          source_scope: ['emails'],
        },
      },
      messages: [
        { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
        {
          event: 'approval_request',
          data: {
            prompt: '確認してください',
            conversation_id: 'conv-workiq-approval-resume',
            plan_markdown: '# Plan v1',
          },
        },
      ],
    }
    sendApproval
      .mockResolvedValueOnce('redirecting')
      .mockResolvedValueOnce('started')
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify(approvalDocument)))
      .mockResolvedValueOnce(new Response(JSON.stringify(approvalDocument)))

    const initialHook = renderHook(() => useSSE())

    await act(async () => {
      await initialHook.result.current.restoreConversation('conv-workiq-approval-resume')
    })

    await act(async () => {
      await initialHook.result.current.approve('approve')
    })

    expect(window.sessionStorage.getItem('workIqPendingApprovalRequest')).toContain('conv-workiq-approval-resume')

    initialHook.unmount()

    renderHook(() => useSSE())

    await waitFor(() => {
      expect(sendApproval).toHaveBeenCalledTimes(2)
    })
    expect(sendApproval).toHaveBeenLastCalledWith(
      'conv-workiq-approval-resume',
      'approve',
      expect.any(Object),
      expect.any(AbortSignal),
      true,
    )
    expect(window.sessionStorage.getItem('workIqPendingApprovalRequest')).toBeNull()
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
    expect(options).toMatchObject({
      cache: 'no-store',
      headers: {
        'Cache-Control': 'no-cache',
      },
    })
  })

  it('ignores passive restore polling while a live SSE request is active', async () => {
    let releaseConnect: (() => void) | null = null
    connectSSE.mockImplementationOnce(async () => {
      await new Promise<void>((resolve) => {
        releaseConnect = resolve
      })
    })

    const fetchMock = vi.mocked(globalThis.fetch)
    const { result } = renderHook(() => useSSE())

    act(() => {
      void result.current.sendMessage('評価結果をもとに改善して')
    })

    await act(async () => {
      await result.current.restoreConversation('conv-passive', { passive: true })
    })

    expect(fetchMock).not.toHaveBeenCalled()

    await act(async () => {
      releaseConnect?.()
    })
  })

  it('reuses the last ETag and skips rebuilding state on 304 restores', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({
        status: 'completed',
        input: '京都の秋プランを企画して',
        messages: [
          { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-etag', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
        ],
      }), { headers: { ETag: 'W/"etag-1"' } }))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { ETag: 'W/"etag-1"' } }))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-etag')
    })

    const previousState = result.current.state

    await act(async () => {
      await result.current.restoreConversation('conv-etag')
    })

    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
    const [, secondOptions] = vi.mocked(globalThis.fetch).mock.calls[1]
    expect(secondOptions).toMatchObject({
      cache: 'no-store',
      headers: {
        'Cache-Control': 'no-cache',
        'If-None-Match': 'W/"etag-1"',
      },
    })
    expect(result.current.state).toBe(previousState)
  })

  it('refetches a cached conversation when switching back from another conversation', async () => {
    vi.mocked(globalThis.fetch).mockImplementation(async (input, init) => {
      const url = String(input)
      const headers = (init && typeof init === 'object' && 'headers' in init
        ? init.headers
        : undefined) as Record<string, string> | undefined

      if (url.endsWith('/api/conversations/conv-new')) {
        if (headers?.['If-None-Match'] === 'W/"etag-new"') {
          return new Response(null, { status: 304, headers: { ETag: 'W/"etag-new"' } })
        }

        return new Response(JSON.stringify({
          status: 'completed',
          input: '新しい会話',
          messages: [
            { event: 'text', data: { content: 'new plan', agent: 'marketing-plan-agent' } },
            { event: 'done', data: { conversation_id: 'conv-new', metrics: { latency_seconds: 9, tool_calls: 1, total_tokens: 110 } } },
          ],
        }), { headers: { ETag: 'W/"etag-new"' } })
      }

      if (url.endsWith('/api/conversations/conv-old')) {
        return new Response(JSON.stringify({
          status: 'completed',
          input: '古い会話',
          messages: [
            { event: 'text', data: { content: 'old plan', agent: 'marketing-plan-agent' } },
            { event: 'done', data: { conversation_id: 'conv-old', metrics: { latency_seconds: 11, tool_calls: 1, total_tokens: 120 } } },
          ],
        }), { headers: { ETag: 'W/"etag-old"' } })
      }

      throw new Error(`Unexpected URL: ${url}`)
    })

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-new')
    })

    await act(async () => {
      await result.current.restoreConversation('conv-old')
    })

    expect(result.current.state.conversationId).toBe('conv-old')
    expect(result.current.state.textContents.at(-1)?.content).toBe('old plan')

    await act(async () => {
      await result.current.restoreConversation('conv-new')
    })

    expect(globalThis.fetch).toHaveBeenCalledTimes(3)
    const [, , thirdCall] = vi.mocked(globalThis.fetch).mock.calls
    expect(thirdCall?.[1]).toMatchObject({
      cache: 'no-store',
      headers: {
        'Cache-Control': 'no-cache',
      },
    })
    expect((thirdCall?.[1] as { headers?: Record<string, string> } | undefined)?.headers?.['If-None-Match']).toBeUndefined()
    expect(result.current.state.conversationId).toBe('conv-new')
    expect(result.current.state.textContents.at(-1)?.content).toBe('new plan')
  })

  it('ignores a stale manual restore once a new live request has started', async () => {
    let resolveRestore: ((response: Response) => void) | null = null
    vi.mocked(globalThis.fetch).mockImplementationOnce(() => new Promise<Response>((resolve) => {
      resolveRestore = resolve
    }))

    const { result } = renderHook(() => useSSE())

    let restorePromise: Promise<void>
    act(() => {
      restorePromise = result.current.restoreConversation('conv-stale-restore')
    })

    act(() => {
      void result.current.sendMessage('新しい依頼を優先して')
    })

    await act(async () => {
      resolveRestore?.(new Response(JSON.stringify({
        status: 'completed',
        input: '古い履歴',
        messages: [
          { event: 'text', data: { content: 'stale plan', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-stale-restore', metrics: { latency_seconds: 12, tool_calls: 1, total_tokens: 120 } } },
        ],
      })))
      await restorePromise
    })

    expect(result.current.state.status).toBe('running')
    expect(result.current.state.userMessages).toEqual(['新しい依頼を優先して'])
    expect(result.current.state.textContents).toEqual([])
    expect(result.current.state.conversationId).toBeNull()
  })

  it('keeps the selected committed version during passive polling updates', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({
        status: 'completed',
        input: '改善版を確認したい',
        metadata: {
          background_updates_pending: true,
        },
        messages: [
          { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-passive-history', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
          { event: 'text', data: { content: '# Plan v2', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-passive-history', metrics: { latency_seconds: 12, tool_calls: 2, total_tokens: 150 } } },
        ],
      })))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        status: 'completed',
        input: '改善版を確認したい',
        metadata: {
          background_updates_pending: true,
        },
        messages: [
          { event: 'text', data: { content: '# Plan v1', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-passive-history', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
          { event: 'text', data: { content: '# Plan v2 updated', agent: 'marketing-plan-agent' } },
          { event: 'done', data: { conversation_id: 'conv-passive-history', metrics: { latency_seconds: 14, tool_calls: 2, total_tokens: 170 } } },
        ],
      })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-passive-history')
    })

    act(() => {
      result.current.restoreVersion(1)
    })

    expect(result.current.state.currentVersion).toBe(1)
    expect(result.current.state.textContents.at(-1)?.content).toBe('# Plan v1')

    await act(async () => {
      await result.current.restoreConversation('conv-passive-history', { passive: true })
    })

    expect(result.current.state.currentVersion).toBe(1)
    expect(result.current.state.textContents.at(-1)?.content).toBe('# Plan v1')
    expect(result.current.state.versions[1].textContents.at(-1)?.content).toBe('# Plan v2 updated')
  })

  it('starts a new conversation while preserving model settings', async () => {
    const { result } = renderHook(() => useSSE())

    act(() => {
      result.current.updateSettings({
        ...DEFAULT_SETTINGS,
        model: 'gpt-5.4',
        managerApprovalEnabled: true,
        managerEmail: 'manager@example.com',
      })
      result.current.updateConversationSettings({
        workIqEnabled: true,
        workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
      })
    })

    await act(async () => {
      await result.current.sendMessage('次の会話の前に一度実行して')
    })

    act(() => {
      result.current.startNewConversation()
    })

    expect(result.current.state.status).toBe('idle')
    expect(result.current.state.conversationId).toBeNull()
    expect(result.current.state.userMessages).toEqual([])
    expect(result.current.state.versions).toEqual([])
    expect(result.current.state.settings).toMatchObject({
      model: 'gpt-5.4',
      managerApprovalEnabled: true,
      managerEmail: 'manager@example.com',
    })
    expect(result.current.state.conversationSettings).toEqual({
      workIqEnabled: true,
      workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
    })
    expect(result.current.state.draftConversationSettings).toEqual({
      workIqEnabled: true,
      workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
    })
  })

  it('restores a saved conversation with its locked Work IQ state without mutating the draft setting', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'completed',
      metadata: {
        work_iq_session: {
          enabled: true,
          status: 'auth_required',
          source_scope: ['meeting_notes', 'documents_notes'],
        },
      },
      messages: [],
    })))

    const { result } = renderHook(() => useSSE())

    act(() => {
      result.current.updateConversationSettings({
        workIqEnabled: false,
        workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
      })
    })

    await act(async () => {
      await result.current.restoreConversation('conv-workiq-history')
    })

    expect(result.current.state.conversationSettings).toEqual({
      workIqEnabled: true,
      workIqSourceScope: ['meeting_notes', 'documents_notes'],
    })
    expect(result.current.state.workIq.status).toBe('sign_in_required')
    expect(result.current.state.draftConversationSettings).toEqual({
      workIqEnabled: false,
      workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
    })

    act(() => {
      result.current.startNewConversation()
    })

    expect(result.current.state.conversationSettings).toEqual({
      workIqEnabled: false,
      workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
    })
    expect(result.current.state.workIq.status).toBe('off')
  })

  it('fails closed instead of restoring an active Work IQ conversation without delegated auth', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'completed',
      metadata: {
        work_iq_session: {
          enabled: true,
          status: 'completed',
          source_scope: ['emails'],
        },
      },
      messages: [],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-workiq-auth')
    })

    expect(result.current.state.workIq.workIqEnabled).toBe(true)

    getDelegatedApiAuth.mockResolvedValueOnce({ headers: {}, status: 'unavailable' })

    await act(async () => {
      await result.current.restoreConversation('conv-workiq-auth')
    })

    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    expect(result.current.state.status).toBe('error')
    expect(result.current.state.error).toEqual(expect.objectContaining({
      code: 'WORKIQ_AUTH_UNAVAILABLE',
    }))
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

    expect(result.current.state.toolEvents).toHaveLength(1)
    expect(result.current.state.toolEvents[0]).toMatchObject({
      tool: 'search_sales_history',
      status: 'completed',
      agent: 'data-search-agent',
      version: 1,
      step_key: 'data-search-agent',
    })
  })

  it('redirects to Foundry consent when a Work IQ tool_event carries a camelCase consent link', async () => {
    const assignSpy = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign: assignSpy })
    connectSSE.mockImplementationOnce(async (_message, handlers) => {
      handlers.tool_event?.({
        tool: 'workiq_foundry_tool',
        status: 'auth_required',
        agent: 'marketing-plan-agent',
        provider: 'foundry',
        source: 'workiq',
        consentLink: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
      })
    })

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.sendMessage('沖縄プランを企画して')
    })

    expect(assignSpy).toHaveBeenCalledWith('https://login.microsoftonline.com/common/oauth2/v2.0/authorize')
    vi.unstubAllGlobals()
  })

  it('redirects to Foundry consent when WORKIQ_AUTH_REQUIRED error includes a consent link', async () => {
    const assignSpy = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign: assignSpy })
    connectSSE.mockImplementationOnce(async (_message, handlers) => {
      handlers.error?.({
        message: 'Foundry Work IQ の同意が必要です。',
        code: 'WORKIQ_AUTH_REQUIRED',
        consentLink: 'https://login.microsoftonline.com/common/oauth2/v2.0/authorize?prompt=consent',
      })
    })

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.sendMessage('沖縄プランを企画して')
    })

    expect(assignSpy).toHaveBeenCalledWith('https://login.microsoftonline.com/common/oauth2/v2.0/authorize?prompt=consent')
    expect(result.current.state.status).not.toBe('error')
    vi.unstubAllGlobals()
  })

  it('blocks unsafe Work IQ auth redirects from tool events', async () => {
    const assignSpy = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign: assignSpy })
    connectSSE.mockImplementationOnce(async (_message, handlers) => {
      handlers.tool_event?.({
        tool: 'workiq_foundry_tool',
        status: 'auth_required',
        agent: 'marketing-plan-agent',
        provider: 'foundry',
        source: 'workiq',
        consentLink: 'https://evil.example/consent',
      })
    })

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.sendMessage('沖縄プランを企画して')
    })

    expect(assignSpy).not.toHaveBeenCalled()
    expect(result.current.state.status).toBe('error')
    expect(result.current.state.error?.code).toBe('WORKIQ_AUTH_REDIRECT_BLOCKED')
    vi.unstubAllGlobals()
  })

  it('blocks non-https Work IQ auth redirects from errors', async () => {
    const assignSpy = vi.fn()
    vi.stubGlobal('location', { ...window.location, assign: assignSpy })
    connectSSE.mockImplementationOnce(async (_message, handlers) => {
      handlers.error?.({
        message: 'Foundry Work IQ の同意が必要です。',
        code: 'WORKIQ_AUTH_REQUIRED',
        consentLink: 'javascript:alert(1)',
      })
    })

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.sendMessage('沖縄プランを企画して')
    })

    expect(assignSpy).not.toHaveBeenCalled()
    expect(result.current.state.status).toBe('error')
    expect(result.current.state.error?.code).toBe('WORKIQ_AUTH_REDIRECT_BLOCKED')
    vi.unstubAllGlobals()
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
    expect(result.current.state.toolEvents.at(-1)).toMatchObject({
      tool: 'web_search',
      status: 'completed',
      agent: 'marketing-plan-agent',
      version: 2,
      step_key: 'marketing-plan-agent',
    })
  })

  it('restores MCP tool metadata from conversation history', async () => {
    vi.mocked(globalThis.fetch).mockResolvedValueOnce(new Response(JSON.stringify({
      status: 'completed',
      input: '品質評価をもとに改善して',
      messages: [
        { event: 'text', data: { content: 'plan v1', agent: 'marketing-plan-agent' } },
        { event: 'done', data: { conversation_id: 'conv-mcp-restore', metrics: { latency_seconds: 10, tool_calls: 1, total_tokens: 100 } } },
        { event: 'tool_event', data: { tool: 'generate_improvement_brief', status: 'failed', agent: 'improvement-mcp', source: 'mcp', fallback: 'legacy_prompt' } },
      ],
    })))

    const { result } = renderHook(() => useSSE())

    await act(async () => {
      await result.current.restoreConversation('conv-mcp-restore')
    })

    expect(result.current.state.toolEvents).toContainEqual(expect.objectContaining({
      tool: 'generate_improvement_brief',
      status: 'failed',
      agent: 'improvement-mcp',
      source: 'mcp',
      fallback: 'legacy_prompt',
      version: 2,
      step_key: 'marketing-plan-agent',
    }))
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
