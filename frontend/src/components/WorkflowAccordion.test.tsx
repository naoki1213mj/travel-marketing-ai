import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { TextContent, ToolEvent } from '../hooks/useSSE'
import { WorkflowAccordion } from './WorkflowAccordion'

const t = (key: string) => ({
  'step.data_search': 'データ分析',
  'step.marketing_plan': '施策生成',
  'step.regulation': '規制チェック',
  'step.brochure': '販促物生成',
  'status.running': '実行中',
  'workflow.brochure.ready': 'ブローシャと画像の生成が完了しました。',
  'workflow.brochure.preview_hint': '右側のタブで確認できます。',
  'workflow.video.ready': 'アバター動画の生成が完了しました。',
  'workflow.video.running': 'アバター動画を生成中…',
  'workflow.round': 'ラウンド {n}',
  'workflow.tool_count': 'ツール {n}件',
  'workflow.tool_none': 'このステップではツール呼び出しログを取得できませんでした。モデル推論のみで完了している可能性があります。',
  'round.initial': '初回実行',
  'round.improvement': '改善',
  'section.analysis': 'データ分析結果',
  'section.regulation': 'レギュレーションチェック',
  'tool.search_sales_history': '販売履歴検索',
  'tool.web_search': 'Web 検索',
  'tool.check_ng_expressions': 'NG 表現チェック',
  'tool.check_travel_law_compliance': '旅行業法チェック',
  'tool.generate_hero_image': 'ヒーロー画像生成',
  'tool.generate_banner_image': 'バナー画像生成',
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

describe('WorkflowAccordion', () => {
  it('allows opening a completed past round and reading its content', () => {
    render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={textContents}
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

  it('shows a friendly collapsed summary for brochure steps instead of raw html', () => {
    render(
      <WorkflowAccordion
        agentProgress={null}
        textContents={textContents}
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
})
