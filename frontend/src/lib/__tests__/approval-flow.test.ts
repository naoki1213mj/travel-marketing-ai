import { describe, expect, it } from 'vitest'
import { isApprovalResponseText, shouldHidePlanDuringPostApprovalRevision } from '../approval-flow'

describe('approval-flow', () => {
  it('detects approval keywords consistently', () => {
    expect(isApprovalResponseText('承認')).toBe(true)
    expect(isApprovalResponseText('approve')).toBe(true)
    expect(isApprovalResponseText('キャッチコピーをもっと明るく')).toBe(false)
  })

  it('hides the plan while post-approval revision is pending', () => {
    expect(shouldHidePlanDuringPostApprovalRevision({
      status: 'running',
      hasApprovalRequest: false,
      hasRevisionContent: false,
      hasRegulationStageStarted: true,
    })).toBe(true)
  })

  it('keeps the plan visible before post-approval revision starts', () => {
    expect(shouldHidePlanDuringPostApprovalRevision({
      status: 'running',
      hasApprovalRequest: false,
      hasRevisionContent: false,
      hasRegulationStageStarted: false,
    })).toBe(false)
  })

  it('keeps the revised plan visible once it is ready', () => {
    expect(shouldHidePlanDuringPostApprovalRevision({
      status: 'running',
      hasApprovalRequest: false,
      hasRevisionContent: true,
      hasRegulationStageStarted: true,
    })).toBe(false)
  })
})
