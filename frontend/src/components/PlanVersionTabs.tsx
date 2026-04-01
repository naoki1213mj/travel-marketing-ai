interface PlanVersion {
  label: string
  content: string
}

interface PlanVersionTabsProps {
  versions: PlanVersion[]
  activeIndex?: number
  onChangeIndex?: (index: number) => void
}

export function PlanVersionTabs({ versions, activeIndex, onChangeIndex }: PlanVersionTabsProps) {
  const active = activeIndex ?? versions.length - 1

  if (versions.length <= 1) return null

  return (
    <div className="mb-3 flex items-center gap-1 rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] p-1" role="toolbar" aria-label="Plan versions">
      {versions.map((v, i) => (
        <button
          key={i}
          type="button"
          onClick={() => onChangeIndex?.(i)}
          className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
            i === active
              ? 'bg-[var(--accent-strong)] text-white'
              : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'
          }`}
        >
          {v.label}
        </button>
      ))}
    </div>
  )
}
