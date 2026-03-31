import { useEffect, useState } from 'react'

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
  const [internal, setInternal] = useState(versions.length - 1)
  const active = activeIndex ?? internal

  // 新バージョン追加時に最新に切り替え
  useEffect(() => {
    const latest = versions.length - 1
    if (onChangeIndex) onChangeIndex(latest)
    else setInternal(latest)
  }, [versions.length, onChangeIndex])

  if (versions.length <= 1) return null

  return (
    <div className="mb-3 flex items-center gap-1 rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] p-1">
      {versions.map((v, i) => (
        <button
          key={i}
          onClick={() => onChangeIndex ? onChangeIndex(i) : setInternal(i)}
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

/** 改善前/後のバージョンラベルを付けたプランリストを構築する */
export function buildPlanVersions(
  textContents: Array<{ agent?: string; content?: string }>,
  t: (key: string) => string,
): PlanVersion[] {
  const plans = textContents.filter(c => c.agent === 'marketing-plan-agent' && c.content)
  if (plans.length <= 1) return []

  return plans.map((p, i) => ({
    label: i === 0 ? t('eval.version.original') : `${t('eval.version.refined')} ${i > 1 ? i : ''}`.trim(),
    content: p.content || '',
  }))
}
