interface VersionSelectorProps {
  versions: number[]
  current: number
  onChange: (version: number) => void
  t: (key: string) => string
  pendingVersion?: number | null
  viewingPending?: boolean
  onSelectPending?: () => void
}

export function VersionSelector({
  versions,
  current,
  onChange,
  t,
  pendingVersion = null,
  viewingPending = false,
  onSelectPending,
}: VersionSelectorProps) {
  if (versions.length <= 1 && !pendingVersion) return null

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-[var(--text-muted)]">{t('version.label')}:</span>
      <div className="flex gap-1">
        {versions.map(v => (
          <button
            key={v}
            type="button"
            onClick={() => onChange(v)}
            className={`rounded-full px-2.5 py-1 text-xs font-medium
              ${v === current && !viewingPending
                ? 'bg-[var(--accent-soft)] text-[var(--accent-strong)]'
                : 'bg-[var(--panel-strong)] text-[var(--text-muted)] hover:text-[var(--text-primary)]'
              }`}
          >
            v{v}
          </button>
        ))}
        {pendingVersion && (
          <button
            type="button"
            onClick={onSelectPending}
            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium transition-colors
              ${viewingPending
                ? 'bg-[var(--accent-soft)] text-[var(--accent-strong)]'
                : 'bg-[var(--panel-strong)] text-[var(--text-muted)] hover:text-[var(--text-primary)]'
              }`}
          >
            <span className="h-2 w-2 animate-spin rounded-full border border-[var(--accent-strong)] border-t-transparent" />
            {t('version.generating').replace('{n}', String(pendingVersion))}
          </button>
        )}
      </div>
    </div>
  )
}
