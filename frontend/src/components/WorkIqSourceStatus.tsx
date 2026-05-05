import type { WorkIqSourceMetadata } from '../lib/event-schemas'
import type { WorkIqSourceScope, WorkIqUiStatus } from './SettingsPanel'

type WorkIqSourceDisplayStatus =
  | 'off'
  | 'ready'
  | 'sign_in_required'
  | 'consent_required'
  | 'unavailable'
  | 'connector_used'
  | 'used'

interface WorkIqSourceStatusProps {
  enabled: boolean
  selectedSources: WorkIqSourceScope[]
  status: WorkIqUiStatus
  sourceMetadata?: WorkIqSourceMetadata[]
  briefSummary?: string
  t: (key: string) => string
}

const STATUS_STYLES: Record<WorkIqSourceDisplayStatus, string> = {
  off: 'border-[var(--panel-border)] bg-[var(--panel-strong)] text-[var(--text-muted)]',
  ready: 'border-[var(--accent)]/20 bg-[var(--accent-soft)] text-[var(--accent-strong)]',
  sign_in_required: 'border-amber-300/80 bg-amber-100/80 text-amber-800 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-200',
  consent_required: 'border-violet-300/80 bg-violet-100/80 text-violet-800 dark:border-violet-700/60 dark:bg-violet-950/40 dark:text-violet-200',
  unavailable: 'border-rose-300/80 bg-rose-100/80 text-rose-800 dark:border-rose-700/60 dark:bg-rose-950/40 dark:text-rose-200',
  connector_used: 'border-sky-300/80 bg-sky-100/80 text-sky-800 dark:border-sky-700/60 dark:bg-sky-950/40 dark:text-sky-200',
  used: 'border-emerald-300/70 bg-emerald-100/80 text-emerald-800 dark:border-emerald-700/60 dark:bg-emerald-950/40 dark:text-emerald-200',
}

const ALL_WORKIQ_SOURCES: WorkIqSourceScope[] = ['meeting_notes', 'emails', 'teams_chats', 'documents_notes']

function normalizeSourceStatus(metadata: WorkIqSourceMetadata | undefined): WorkIqSourceDisplayStatus | null {
  const status = String(metadata?.status || '').trim().toLowerCase()
  switch (status) {
    case 'completed':
    case 'ok':
    case 'used':
      return 'used'
    case 'connector_used':
    case 'connector_completed':
      return 'connector_used'
    case 'auth_required':
    case 'sign_in_required':
      return 'sign_in_required'
    case 'consent_required':
      return 'consent_required'
    case 'failed':
    case 'error':
    case 'timeout':
    case 'identity_mismatch':
    case 'unavailable':
      return 'unavailable'
    case 'pending':
    case 'running':
    case 'ready':
      return 'ready'
    default:
      return metadata?.count && metadata.count > 0 ? 'used' : null
  }
}

function resolveSourceStatus(
  enabled: boolean,
  selected: boolean,
  status: WorkIqUiStatus,
  metadata: WorkIqSourceMetadata | undefined,
): WorkIqSourceDisplayStatus {
  if (!enabled || !selected) return 'off'
  const metadataStatus = normalizeSourceStatus(metadata)
  if (metadataStatus) return metadataStatus
  if (status === 'enabled') return 'ready'
  if (status === 'sign_in_required' || status === 'consent_required' || status === 'unavailable') return status
  return 'ready'
}

function buildCountLabel(count: number | undefined, t: (key: string) => string): string | null {
  if (count === undefined) return null
  return t('settings.workiq.sourceStatus.count').replace('{count}', String(count))
}

export function WorkIqSourceStatus({
  enabled,
  selectedSources,
  status,
  sourceMetadata,
  briefSummary,
  t,
}: WorkIqSourceStatusProps) {
  const selected = new Set(selectedSources)
  const metadataBySource = new Map((sourceMetadata ?? []).map(item => [item.source, item]))
  const singleSourceSummary = sourceMetadata?.length === 1 ? sourceMetadata[0] : null

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--text-muted)]">
          {t('settings.workiq.sourceStatus.title')}
        </p>
        <span className="text-[10px] text-[var(--text-muted)]">{t('settings.workiq.sourceStatus.safeOnly')}</span>
      </div>

      {briefSummary && (
        <div className="rounded-xl border border-[var(--panel-border)] bg-[var(--surface)] px-3 py-2">
          <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--text-muted)]">
            {t('settings.workiq.sourceStatus.summary')}
          </p>
          <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{briefSummary}</p>
        </div>
      )}

      <div className="grid gap-2 sm:grid-cols-2">
        {ALL_WORKIQ_SOURCES.map((source) => {
          const metadata = metadataBySource.get(source)
          const sourceStatus = resolveSourceStatus(enabled, selected.has(source), status, metadata)
          const countLabel = buildCountLabel(metadata?.count, t)
          const sourcePreview = metadata?.summary
            ?? metadata?.preview
            ?? (singleSourceSummary?.source === source ? briefSummary : undefined)

          return (
            <div
              key={source}
              className="rounded-xl border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-2.5"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-[var(--text-primary)]">
                  {metadata?.label || t(`settings.workiq.source.${source}`)}
                </span>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] ${STATUS_STYLES[sourceStatus]}`}>
                  {t(`settings.workiq.sourceStatus.${sourceStatus}`)}
                </span>
                {countLabel && (
                  <span className="rounded-full border border-[var(--panel-border)] bg-[var(--surface)] px-2 py-0.5 text-[10px] text-[var(--text-muted)]">
                    {countLabel}
                  </span>
                )}
              </div>
              <p className="mt-2 text-[11px] leading-5 text-[var(--text-muted)]">
                {sourcePreview || t('settings.workiq.sourceStatus.noPreview')}
              </p>
            </div>
          )
        })}
      </div>
    </div>
  )
}
