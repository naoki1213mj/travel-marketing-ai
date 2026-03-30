import type { AgentProgress } from '../hooks/useSSE'

const STEPS = [
  { key: 'data-search-agent', label: 'step.data_search', icon: '📊' },
  { key: 'marketing-plan-agent', label: 'step.marketing_plan', icon: '📝' },
  { key: 'approval', label: 'step.approval', icon: '✅' },
  { key: 'regulation-check-agent', label: 'step.regulation', icon: '⚖️' },
  { key: 'brochure-gen-agent', label: 'step.brochure', icon: '🎨' },
]

interface PipelineStepperProps {
  progress: AgentProgress | null
  t: (key: string) => string
}

export function PipelineStepper({ progress, t }: PipelineStepperProps) {
  const currentStep = progress ? progress.step : 0
  const currentAgent = progress?.agent || ''

  return (
    <div className="flex items-center gap-1 py-3">
      {STEPS.map((step, i) => {
        const stepNum = i + 1
        const isActive = step.key === currentAgent && progress?.status === 'running'
        const isCompleted = stepNum < currentStep ||
          (stepNum === currentStep && progress?.status === 'completed') ||
          (step.key === 'approval' && currentStep > 2)
        const isPending = !isActive && !isCompleted

        return (
          <div key={step.key} className="flex items-center gap-1">
            {i > 0 && (
              <div className={`h-0.5 w-6 ${isCompleted ? 'bg-[var(--accent)]' : 'bg-[var(--panel-border)]'}`} />
            )}
            <div className="flex flex-col items-center gap-1">
              <div
                className={`flex h-8 w-8 items-center justify-center rounded-full text-sm
                  ${isCompleted ? 'bg-[var(--accent)] text-white' : ''}
                  ${isActive ? 'animate-pulse bg-[var(--accent-soft)] text-[var(--accent-strong)] ring-2 ring-[var(--accent)]/40' : ''}
                  ${isPending ? 'bg-[var(--panel-strong)] text-[var(--text-muted)]' : ''}`}
              >
                {isCompleted ? '✓' : step.icon}
              </div>
              <span className={`text-xs whitespace-nowrap
                ${isActive ? 'font-medium text-[var(--accent-strong)]' : 'text-[var(--text-muted)]'}`}>
                {t(step.label)}
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
