import { fireEvent, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

const mockUseSSE = vi.fn()

vi.mock('./hooks/useSSE', () => ({
  useSSE: () => mockUseSSE(),
}))

vi.mock('./hooks/useTheme', () => ({
  useTheme: () => ({
    theme: 'light',
    setTheme: vi.fn(),
  }),
}))

vi.mock('./hooks/useI18n', () => ({
  useI18n: () => ({
    locale: 'ja',
    setLocale: vi.fn(),
    t: (key: string) => {
      const labels: Record<string, string> = {
        'app.title': 'Travel Marketing AI',
        'status.running': 'Running',
        'panel.workflow': 'Workflow',
        'panel.workflow.subtitle': 'Workflow subtitle',
        'panel.workflow.hint.progress': 'Progress hint',
        'panel.workflow.hint.approval': 'Approval hint',
        'panel.workflow.hint.rounds': 'Rounds hint',
        'panel.composer': 'Composer',
        'panel.composer.subtitle': 'Composer subtitle',
        'panel.preview': 'Preview',
        'panel.preview.subtitle': 'Preview subtitle',
        'panel.preview.hint.version': 'Version hint',
        'panel.preview.hint.plan': 'Plan hint',
        'panel.preview.hint.assets': 'Assets hint',
        'tab.plan': 'Plan',
        'tab.evaluation': 'Evaluation',
        'tab.brochure': 'Brochure',
        'tab.images': 'Images',
        'tab.video': 'Video',
        'input.send': 'Send',
        'input.placeholder': 'Placeholder',
        'input.label': 'Input',
        'refine.placeholder': 'Refine',
        'refine.label': 'Refine label',
        'export.plan': 'Export plan',
        'preview.pending.subtitle': 'Pending subtitle',
        'preview.pending.previous': 'Previous {n}',
        'preview.pending.viewing_previous': 'Viewing v{current} while v{pending} is generating',
        'preview.pending.return_live': 'Return to v{n}',
        'preview.pending.waiting': 'Waiting v{n}',
        'version.label': 'Version',
        'version.generating': 'Generating v{n}',
        'step.marketing_plan': 'Plan Generation',
      }
      return labels[key] ?? key
    },
  }),
}))

vi.mock('./hooks/useElapsedTime', () => ({
  useElapsedTime: () => 0,
}))

vi.mock('./components/ArtifactTabs', () => ({
  ArtifactTabs: ({ tabs }: { tabs: Array<{ key: string; content: ReactNode }> }) => (
    <div>
      {tabs.map((tab) => (
        <div key={tab.key} data-testid={`tab-${tab.key}`}>
          {tab.content}
        </div>
      ))}
    </div>
  ),
}))

vi.mock('./components/VersionSelector', () => ({
  VersionSelector: ({
    versions,
    onChange,
    pendingVersion,
    onSelectPending,
  }: {
    versions: number[]
    onChange: (version: number) => void
    pendingVersion?: number | null
    onSelectPending?: () => void
  }) => (
    <div>
      {versions.map((version) => (
        <button key={version} type="button" onClick={() => onChange(version)}>
          v{version}
        </button>
      ))}
      {pendingVersion ? (
        <button type="button" onClick={onSelectPending}>
          pending-{pendingVersion}
        </button>
      ) : null}
    </div>
  ),
}))

vi.mock('./components/EvaluationPanel', () => ({
  EvaluationPanel: ({ artifactVersion, evaluations, query }: { artifactVersion?: number; evaluations?: unknown[]; query?: string }) => (
    <div
      data-testid="evaluation-panel"
      data-version={artifactVersion ? String(artifactVersion) : ''}
      data-evaluations={String(evaluations?.length ?? 0)}
      data-query={query ?? ''}
    />
  ),
}))

vi.mock('./components/ApprovalBanner', () => ({ ApprovalBanner: () => null }))
vi.mock('./components/BrochurePreview', () => ({ BrochurePreview: () => null }))
vi.mock('./components/ConversationHistory', () => ({ ConversationHistory: () => null }))
vi.mock('./components/ImageGallery', () => ({ ImageGallery: () => null }))
vi.mock('./components/InputForm', () => ({ InputForm: () => null }))
vi.mock('./components/LanguageSwitcher', () => ({ LanguageSwitcher: () => null }))
vi.mock('./components/MarkdownView', () => ({ MarkdownView: ({ content }: { content: string }) => <div>{content}</div> }))
vi.mock('./components/PdfUpload', () => ({ PdfUpload: () => null }))
vi.mock('./components/PipelineStepper', () => ({ PipelineStepper: () => null }))
vi.mock('./components/PlanVersionTabs', () => ({ PlanVersionTabs: () => null }))
vi.mock('./components/RefineChat', () => ({ RefineChat: () => null }))
vi.mock('./components/SettingsPanel', () => ({ SettingsPanel: () => null }))
vi.mock('./components/ThemeToggle', () => ({ ThemeToggle: () => null }))
vi.mock('./components/VideoPreview', () => ({ VideoPreview: () => null }))
vi.mock('./components/VoiceInput', () => ({ VoiceInput: () => null }))
vi.mock('./components/WorkflowAccordion', () => ({ WorkflowAccordion: () => null }))

describe('App', () => {
  it('keeps the live pending version selected while a newer version is generating', () => {
    mockUseSSE.mockReturnValue({
      state: {
        status: 'running',
        conversationId: 'conv-1',
        agentProgress: {
          agent: 'marketing-plan-agent',
          status: 'running',
          step: 2,
          total_steps: 5,
        },
        backgroundUpdatesPending: false,
        toolEvents: [],
        textContents: [
          {
            agent: 'marketing-plan-agent',
            content: '# Plan v1',
          },
        ],
        images: [],
        approvalRequest: null,
        metrics: null,
        error: null,
        versions: [
          {
            textContents: [
              {
                agent: 'marketing-plan-agent',
                content: '# Plan v1',
              },
            ],
            images: [],
            toolEvents: [],
            metrics: null,
            evaluations: [
              {
                version: 1,
                round: 1,
                createdAt: '2026-04-03T00:00:00Z',
                result: {
                  builtin: {
                    relevance: { score: 4, reason: 'good' },
                  },
                },
              },
            ],
          },
        ],
        currentVersion: 1,
        pendingVersion: {
          version: 2,
          textOffset: 1,
          imageOffset: 0,
          toolEventOffset: 0,
        },
        settings: {
          model: 'gpt-5-4-mini',
          temperature: 0.7,
          max_tokens: 2000,
          top_p: 1,
        },
        userMessages: ['北海道プランを改善して'],
      },
      sendMessage: vi.fn(),
      approve: vi.fn(),
      reset: vi.fn(),
      restoreVersion: vi.fn(),
      updateSettings: vi.fn(),
      restoreConversation: vi.fn(),
      saveEvaluation: vi.fn(),
    })

    render(<App />)

    expect(screen.queryByTestId('evaluation-panel')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'v1' }))

    expect(screen.getByTestId('evaluation-panel')).toHaveAttribute('data-version', '1')
    expect(screen.getByTestId('evaluation-panel')).toHaveAttribute('data-evaluations', '1')

    fireEvent.click(screen.getByRole('button', { name: 'pending-2' }))

    expect(screen.queryByTestId('evaluation-panel')).toBeNull()
  })

  it('keeps the latest committed evaluation visible after generation completes', () => {
    mockUseSSE.mockReturnValue({
      state: {
        status: 'completed',
        conversationId: 'conv-1',
        agentProgress: {
          agent: 'video-gen-agent',
          status: 'completed',
          step: 5,
          total_steps: 5,
        },
        backgroundUpdatesPending: false,
        toolEvents: [],
        textContents: [
          {
            agent: 'marketing-plan-agent',
            content: '# Plan v1',
          },
        ],
        images: [],
        approvalRequest: null,
        metrics: null,
        error: null,
        versions: [
          {
            textContents: [
              {
                agent: 'marketing-plan-agent',
                content: '# Plan v1',
              },
            ],
            images: [],
            toolEvents: [],
            metrics: null,
            evaluations: [
              {
                version: 1,
                round: 1,
                createdAt: '2026-04-03T00:00:00Z',
                result: {
                  builtin: {
                    relevance: { score: 4, reason: 'good' },
                  },
                },
              },
            ],
          },
        ],
        currentVersion: 1,
        pendingVersion: null,
        settings: {
          model: 'gpt-5-4-mini',
          temperature: 0.7,
          max_tokens: 2000,
          top_p: 1,
        },
        userMessages: ['北海道プランを改善して'],
      },
      sendMessage: vi.fn(),
      approve: vi.fn(),
      reset: vi.fn(),
      restoreVersion: vi.fn(),
      updateSettings: vi.fn(),
      restoreConversation: vi.fn(),
      saveEvaluation: vi.fn(),
    })

    render(<App />)

    expect(screen.getByTestId('evaluation-panel')).toHaveAttribute('data-version', '1')
    expect(screen.getByTestId('evaluation-panel')).toHaveAttribute('data-evaluations', '1')
  })

  it('shows the in-flight plan during second manager approval by default', () => {
    mockUseSSE.mockReturnValue({
      state: {
        status: 'approval',
        conversationId: 'conv-manager-2',
        agentProgress: {
          agent: 'approval',
          status: 'running',
          step: 3,
          total_steps: 5,
        },
        managerApprovalPolling: true,
        backgroundUpdatesPending: false,
        hasManagerApprovalPhase: true,
        toolEvents: [],
        textContents: [
          {
            agent: 'marketing-plan-agent',
            content: '# Plan v1',
          },
          {
            agent: 'marketing-plan-agent',
            content: '# Plan v2',
          },
        ],
        images: [],
        approvalRequest: {
          prompt: '上司承認を待っています',
          conversation_id: 'conv-manager-2',
          plan_markdown: '# Plan v2',
          approval_scope: 'manager',
          manager_email: 'manager@example.com',
        },
        metrics: null,
        error: null,
        versions: [
          {
            textContents: [
              {
                agent: 'marketing-plan-agent',
                content: '# Plan v1',
              },
            ],
            images: [],
            toolEvents: [],
            metrics: null,
            evaluations: [
              {
                version: 1,
                round: 1,
                createdAt: '2026-04-03T00:00:00Z',
                result: {
                  builtin: {
                    relevance: { score: 4, reason: 'good' },
                  },
                },
              },
            ],
          },
        ],
        currentVersion: 1,
        pendingVersion: {
          version: 2,
          textOffset: 1,
          imageOffset: 0,
          toolEventOffset: 0,
        },
        settings: {
          model: 'gpt-5-4-mini',
          temperature: 0.7,
          max_tokens: 2000,
          top_p: 1,
        },
        userMessages: ['北海道プランを改善して'],
      },
      sendMessage: vi.fn(),
      approve: vi.fn(),
      reset: vi.fn(),
      restoreVersion: vi.fn(),
      updateSettings: vi.fn(),
      restoreConversation: vi.fn(),
      saveEvaluation: vi.fn(),
    })

    render(<App />)

    expect(screen.queryByTestId('evaluation-panel')).toBeNull()
    expect(screen.getAllByText('# Plan v2').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: 'v1' }))

    expect(screen.getByTestId('evaluation-panel')).toHaveAttribute('data-version', '1')
    expect(screen.getByTestId('evaluation-panel')).toHaveAttribute('data-evaluations', '1')
  })

  it('renders explicit workflow and preview header hints', () => {
    mockUseSSE.mockReturnValue({
      state: {
        status: 'completed',
        conversationId: 'conv-hints',
        agentProgress: null,
        managerApprovalPolling: false,
        backgroundUpdatesPending: false,
        hasManagerApprovalPhase: false,
        toolEvents: [],
        textContents: [
          {
            agent: 'marketing-plan-agent',
            content: '# Plan v1',
          },
        ],
        images: [],
        approvalRequest: null,
        metrics: null,
        error: null,
        versions: [
          {
            textContents: [
              {
                agent: 'marketing-plan-agent',
                content: '# Plan v1',
              },
            ],
            images: [],
            toolEvents: [],
            metrics: null,
            evaluations: [],
          },
        ],
        currentVersion: 1,
        pendingVersion: null,
        settings: {
          model: 'gpt-5-4-mini',
          temperature: 0.7,
          max_tokens: 2000,
          top_p: 1,
        },
        userMessages: ['北海道プランを改善して'],
      },
      sendMessage: vi.fn(),
      approve: vi.fn(),
      reset: vi.fn(),
      restoreVersion: vi.fn(),
      updateSettings: vi.fn(),
      restoreConversation: vi.fn(),
      saveEvaluation: vi.fn(),
    })

    render(<App />)

    expect(screen.getByText('Progress hint')).toBeInTheDocument()
    expect(screen.getByText('Approval hint')).toBeInTheDocument()
    expect(screen.getByText('Rounds hint')).toBeInTheDocument()
    expect(screen.getByText('Version hint')).toBeInTheDocument()
    expect(screen.getByText('Plan hint')).toBeInTheDocument()
    expect(screen.getByText('Assets hint')).toBeInTheDocument()
  })

  it('passes only the original and latest user intent to the evaluation panel', () => {
    mockUseSSE.mockReturnValue({
      state: {
        status: 'completed',
        conversationId: 'conv-query-history',
        agentProgress: null,
        managerApprovalPolling: false,
        backgroundUpdatesPending: false,
        hasManagerApprovalPhase: false,
        toolEvents: [],
        textContents: [
          {
            agent: 'marketing-plan-agent',
            content: '# Plan v2',
          },
        ],
        images: [],
        approvalRequest: null,
        metrics: null,
        error: null,
        versions: [
          {
            textContents: [
              {
                agent: 'marketing-plan-agent',
                content: '# Plan v2',
              },
            ],
            images: [],
            toolEvents: [],
            metrics: null,
            evaluations: [],
          },
        ],
        currentVersion: 1,
        pendingVersion: null,
        settings: {
          model: 'gpt-5-4-mini',
          temperature: 0.7,
          max_tokens: 2000,
          top_p: 1,
        },
        userMessages: [
          '北海道プランを改善して',
          '価格を強く出しすぎないで',
          '価格訴求を控えて高級感を強めて',
        ],
      },
      sendMessage: vi.fn(),
      approve: vi.fn(),
      reset: vi.fn(),
      restoreVersion: vi.fn(),
      updateSettings: vi.fn(),
      restoreConversation: vi.fn(),
      saveEvaluation: vi.fn(),
    })

    render(<App />)

    expect(screen.getByTestId('evaluation-panel')).toHaveAttribute(
      'data-query',
      '北海道プランを改善して\n\n価格訴求を控えて高級感を強めて',
    )
  })
})
