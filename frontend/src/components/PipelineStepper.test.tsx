import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PipelineStepper } from './PipelineStepper'

const t = (key: string) => ({
  'step.data_search': 'データ分析',
  'step.marketing_plan': '施策生成',
  'step.approval': '承認',
  'step.regulation': '規制チェック',
  'step.plan_revision': '企画書修正',
  'step.manager_approval': '上司承認',
  'step.brochure': '販促物生成',
  'step.video': '動画生成',
}[key] ?? key)

describe('PipelineStepper', () => {
  it('renders an extra manager approval phase when enabled', () => {
    render(
      <PipelineStepper
        progress={{ agent: 'plan-revision-agent', status: 'running', step: 4, total_steps: 5 }}
        t={t}
        showManagerApprovalPhase
      />,
    )

    expect(screen.getByText('上司承認')).toBeInTheDocument()
  })

  it('shows manager approval as the active phase while manager approval is pending', () => {
    render(
      <PipelineStepper
        progress={{ agent: 'approval', status: 'running', step: 3, total_steps: 5 }}
        t={t}
        showManagerApprovalPhase
        managerApprovalActive
      />,
    )

    expect(screen.getByText('上司承認').className).toContain('font-medium')
  })
})
