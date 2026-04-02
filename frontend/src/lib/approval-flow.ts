const APPROVAL_RESPONSE_PATTERN = /(承認|了承|進めて|批准|同意|\bapprove(?:d)?\b|\bok\b|\byes\b|\bgo\b)/i

export type PlanVisibilityStatus = 'idle' | 'running' | 'approval' | 'completed' | 'error'

interface PlanVisibilityParams {
  status: PlanVisibilityStatus
  hasApprovalRequest: boolean
  hasRevisionContent: boolean
  hasRegulationStageStarted: boolean
}

export function isApprovalResponseText(response: string): boolean {
  return APPROVAL_RESPONSE_PATTERN.test(response.trim())
}

export function shouldHidePlanDuringPostApprovalRevision(params: PlanVisibilityParams): boolean {
  if (params.hasRevisionContent || params.hasApprovalRequest || params.status === 'completed') {
    return false
  }

  return params.hasRegulationStageStarted
}
