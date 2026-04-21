import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ImageContent, TextContent, ToolEvent } from '../hooks/useSSE'
import { WorkflowAccordion } from './WorkflowAccordion'

const t = (key: string) => ({
  'step.data_search': 'データ分析',
  'step.marketing_plan': '施策生成',
  'step.regulation': '規制チェック',
  'step.brochure': '販促物生成',
  'step.video': '動画生成',
  'status.running': '実行中',
  'workflow.brochure.ready': 'ブローシャと画像の生成が完了しました。',
  'workflow.brochure.preview_hint': '右側のタブで確認できます。',
  'workflow.video.ready': 'アバター動画の生成が完了しました。',
  'workflow.video.pending': '動画生成イベントを待機中です。',
  'workflow.video.running': 'アバター動画を生成中…',
  'workflow.round': 'ラウンド {n}',
  'workflow.tool_count': 'ツール {n}件',
  'workflow.tool_none': 'このステップではツール呼び出しログを取得できませんでした。モデル推論のみで完了している可能性があります。',
  'workflow.tool_additional': '追加のツールアクティビティ',
  'round.initial': '初回実行',
  'round.improvement': '改善',
  'section.analysis': 'データ分析結果',
  'section.images': '画像',
  'section.regulation': 'レギュレーションチェック',
  'export.image': '画像をダウンロード',
  'preview.unavailable': 'プレビューを利用できません。',
  'tool.search_sales_history': '販売履歴検索',
  'tool.web_search': 'Web 検索',
  'tool.check_ng_expressions': 'NG 表現チェック',
  'tool.check_travel_law_compliance': '旅行業法チェック',
  'tool.generate_improvement_brief': '改善ブリーフ生成',
  'tool.generate_hero_image': 'ヒーロー画像生成',
  'tool.generate_banner_image': 'バナー画像生成',
  'tool.source.mcp': 'Azure Functions MCP',
  'tool.source.foundry': 'Microsoft Foundry',
  'tool.source.workiq': 'Work IQ',
  'tool.meta.inferred': '推定',
  'tool.fallback.legacy_prompt': '従来経路へフォールバック',
  'error.retry': '再試行',
}[key] ?? key)

const textContents: TextContent[] = [
  { agent: 'data-search-agent', content: '初回分析の結果です。' },
  { agent: 'marketing-plan-agent', content: '# 初版企画書' },
  { agent: 'regulation-check-agent', content: '初回の規制チェック結果です。' },
  { agent: 'brochure-gen-agent', content: '<!DOCTYPE html><html><body>v1</body></html>', content_type: 'html' },
  { agent: 'marketing-plan-agent', content: '# 改善版企画書' },
  { agent: 'regulation-check-agent', content: '改善後の規制チェック結果です。' },
  { agent: 'brochure-gen-agent', content: '<!DOCTYPE html><html><body>v2</body></html>', content_type: 'html' },
]

const toolEvents: ToolEvent[] = [
  { tool: 'search_sales_history', status: 'completed', agent: 'data-search-agent', version: 1 },
  { tool: 'web_search', status: 'completed', agent: 'marketing-plan-agent', version: 1 },
  { tool: 'check_ng_expressions', status: 'completed', agent: 'regulation-check-agent', version: 1 },
  { tool: 'check_travel_law_compliance', status: 'completed', agent: 'regulation-check-agent', version: 1 },
  { tool: 'generate_hero_image', status: 'completed', agent: 'brochure-gen-agent', version: 1 },
  { tool: 'generate_banner_image', status: 'completed', agent: 'brochure-gen-agent', version: 1 },
  { tool: 'web_search', status: 'completed', agent: 'marketing-plan-agent', version: 2 },
  { tool: 'check_ng_expressions', status: 'completed', agent: 'regulation-check-agent', version: 2 },
  { tool: 'check_travel_law_compliance', status: 'completed', agent: 'regulation-check-agent', version: 2 },
  { tool: 'generate_hero_image', status: 'completed', agent: 'brochure-gen-agent', version: 2 },
  { tool: 'generate_banner_image', status: 'completed', agent: 'brochure-gen-agent', version: 2 },
]

const images: ImageContent[] = [
  {
    agent: 'data-search-agent',
    alt: 'データ分析グラフ（Code Interpreter）',
    url: 'data:image/png;base64,analysischart',
  },
]

describe('WorkflowAccordion', () => {
  it('clears manual section toggles when the conversation changes', () => {
    const singleRoundContents: TextContent[] = textContents.slice(0, 4)
    const singleRoundToolEvents: ToolEvent[] = toolEvents.filter(event => event.version === 1)

    const { rerender } = render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={singleRoundContents}
        images={images}
        toolEvents={singleRoundToolEvents}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
        conversationKey="conv-a"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /施策生成/ }))

    expect(screen.getByText('初版企画書')).toBeInTheDocument()

    rerender(
      <WorkflowAccordion
        agentProgress={null}
        textContents={singleRoundContents}
        images={images}
        toolEvents={singleRoundToolEvents}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
        conversationKey="conv-b"
      />,
    )

    expect(screen.queryByText('初版企画書')).toBeNull()
  })

  it('allows opening a completed past round and reading its content', () => {
    render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={textContents}
        images={images}
        toolEvents={toolEvents}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
      />,
    )

    fireEvent.click(screen.getByText('初回実行'))
    fireEvent.click(screen.getAllByRole('button', { name: /施策生成/ })[0])

    expect(screen.getByText('初版企画書')).toBeInTheDocument()
  })

  it('shows only the latest round tool badges inside the current brochure step', () => {
    render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={textContents}
        images={images}
        toolEvents={toolEvents}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
      />,
    )

    fireEvent.click(screen.getAllByRole('button', { name: /販促物生成/ }).at(-1) as HTMLButtonElement)

    expect(screen.getAllByText('ヒーロー画像生成')).toHaveLength(1)
    expect(screen.getAllByText('バナー画像生成')).toHaveLength(1)
  })

  it('shows improvement MCP usage inside the latest refinement step', () => {
    const mcpToolEvents: ToolEvent[] = [
      ...toolEvents,
      { tool: 'generate_improvement_brief', status: 'completed', agent: 'improvement-mcp', source: 'mcp', version: 2, step_key: 'marketing-plan-agent' },
    ]

    const { container } = render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={textContents}
        images={images}
        toolEvents={mcpToolEvents}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
      />,
    )

    fireEvent.click(screen.getAllByRole('button', { name: /施策生成/ }).at(-1) as HTMLButtonElement)

    expect(screen.getByText('改善ブリーフ生成')).toBeInTheDocument()
      expect(screen.getAllByText('Azure Functions MCP')).toHaveLength(2)
    expect(container.querySelector('[data-step-source="mcp"]')).not.toBeNull()
    expect(container.querySelector('[data-tool-name="generate_improvement_brief"][data-tool-source="mcp"]')).not.toBeNull()
    expect(container.querySelector('[data-tool-name="generate_improvement_brief"][data-tool-provider="azure-functions-mcp"]')).not.toBeNull()
  })

  it('shows a dedicated Foundry Work IQ indicator on the marketing-plan step', () => {
    const workIqToolEvents: ToolEvent[] = [
      ...toolEvents,
      {
        tool: 'workiq_foundry_tool',
        status: 'completed',
        agent: 'marketing-plan-agent',
        version: 2,
        source: 'workiq',
        provider: 'foundry',
        display_name: 'Work IQ context tools',
        source_scope: ['meeting_notes', 'emails'],
      },
    ]

    const { container } = render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={textContents}
        images={images}
        toolEvents={workIqToolEvents}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
      />,
    )

    expect(screen.getAllByText('Microsoft Foundry Work IQ').length).toBeGreaterThan(0)
    expect(container.querySelector('[data-step-source="workiq-foundry"]')).not.toBeNull()

    fireEvent.click(screen.getAllByRole('button', { name: /施策生成/ }).at(-1) as HTMLButtonElement)

    expect(screen.getByText('Work IQ context tools')).toBeInTheDocument()
    expect(container.querySelector('[data-tool-kind="foundry-workiq"]')).not.toBeNull()
  })

  it('shows a friendly collapsed summary for brochure steps instead of raw html', () => {
    render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={textContents}
        images={images}
        toolEvents={toolEvents}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
      />,
    )

    expect(screen.getAllByText('ブローシャと画像の生成が完了しました。').length).toBeGreaterThan(0)
    expect(screen.queryByText(/<!DOCTYPE html>/)).toBeNull()
  })

  it('shows an explicit message when no tool log is available for a step', () => {
    render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={textContents}
        images={images}
        toolEvents={[]}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
      />,
    )

    fireEvent.click(screen.getAllByRole('button', { name: /施策生成/ }).at(-1) as HTMLButtonElement)

    expect(screen.getByText('このステップではツール呼び出しログを取得できませんでした。モデル推論のみで完了している可能性があります。')).toBeInTheDocument()
  })

  it('shows the latest avatar video status message instead of a perpetual running label', () => {
    const contentsWithVideoTimeout: TextContent[] = [
      ...textContents,
      {
        agent: 'video-gen-agent',
        content: '{"status":"timeout","message":"⚠️ アバター動画の生成完了を確認できませんでした。Photo Avatar ジョブがタイムアウトまたは失敗した可能性があります。"}',
      },
    ]

    render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={contentsWithVideoTimeout}
        images={images}
        toolEvents={toolEvents}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
      />,
    )

    fireEvent.click(screen.getAllByRole('button', { name: /動画生成/ }).at(-1) as HTMLButtonElement)

    expect(screen.getByText('⚠️ アバター動画の生成完了を確認できませんでした。Photo Avatar ジョブがタイムアウトまたは失敗した可能性があります。')).toBeInTheDocument()
    expect(screen.queryByText('アバター動画を生成中…')).toBeNull()
  })

  it('collapses duplicate running/completed tool events and shows the latest count', () => {
    const duplicatedToolEvents: ToolEvent[] = [
      { tool: 'check_ng_expressions', status: 'running', agent: 'regulation-check-agent', version: 1, started_at: '2026-04-19T05:00:00Z' },
      { tool: 'check_ng_expressions', status: 'completed', agent: 'regulation-check-agent', version: 1, started_at: '2026-04-19T05:00:00Z', finished_at: '2026-04-19T05:00:01Z' },
      { tool: 'check_travel_law_compliance', status: 'running', agent: 'regulation-check-agent', version: 1, started_at: '2026-04-19T05:00:02Z' },
      { tool: 'check_travel_law_compliance', status: 'completed', agent: 'regulation-check-agent', version: 1, started_at: '2026-04-19T05:00:02Z', finished_at: '2026-04-19T05:00:03Z' },
    ]

    const { container } = render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={textContents.slice(0, 4)}
        images={images}
        toolEvents={duplicatedToolEvents}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /規制チェック/ }))

    expect(screen.getByText('ツール 2件')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-tool-name="check_ng_expressions"]')).toHaveLength(1)
    expect(container.querySelectorAll('[data-tool-name="check_travel_law_compliance"]')).toHaveLength(1)
    expect(container.querySelector('[data-tool-name="check_ng_expressions"][data-tool-status="completed"]')).not.toBeNull()
    expect(container.querySelector('[data-tool-name="check_travel_law_compliance"][data-tool-status="completed"]')).not.toBeNull()
  })

  it('renders actual analysis images and strips broken markdown image markup', () => {
    const contentsWithBrokenImage: TextContent[] = [
      {
        agent: 'data-search-agent',
        content: '分析本文です。\n\n![売上・評価の可視化](broken-chart.png)',
      },
      ...textContents.slice(1, 4),
    ]

    const { container } = render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={contentsWithBrokenImage}
        images={images}
        toolEvents={toolEvents.filter(event => event.version === 1)}
        metrics={null}
        error={null}
        onRetry={vi.fn()}
        t={t}
        locale="ja"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /データ分析/ }))

    expect(container.querySelector('img[src="broken-chart.png"]')).toBeNull()
    expect(container.querySelector('img[src^="data:image/png;base64,"]')).not.toBeNull()
  })
})
