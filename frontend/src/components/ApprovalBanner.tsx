import { useState } from 'react'

interface ApprovalBannerProps {
  request: { prompt: string; plan_markdown?: string; conversation_id: string }
  onApprove: (response: string) => void
  t: (key: string) => string
}

export function ApprovalBanner({ request, onApprove, t }: ApprovalBannerProps) {
  const [mode, setMode] = useState<'action' | 'revise'>('action')
  const [revision, setRevision] = useState('')

  return (
    <div className="border-t-2 border-amber-400 bg-gradient-to-r from-amber-50 to-orange-50 dark:from-amber-950/30 dark:to-orange-950/30 px-5 py-4 shadow-[0_-4px_20px_rgba(251,191,36,0.12)]">
      <div className="flex items-center gap-3 mb-3">
        <span className="relative flex h-3 w-3">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
          <span className="relative inline-flex h-3 w-3 rounded-full bg-amber-500" />
        </span>
        <h3 className="text-sm font-semibold text-amber-800 dark:text-amber-200">
          ⚡ {t('approval.title')}
        </h3>
      </div>

      {mode === 'action' ? (
        <div className="flex items-center gap-3">
          <p className="flex-1 text-sm text-[var(--text-secondary)]">{request.prompt}</p>
          <button
            onClick={() => setMode('revise')}
            className="rounded-full border border-[var(--panel-border)] bg-white/80 dark:bg-white/10 px-4 py-2 text-sm font-medium text-[var(--text-secondary)] hover:bg-white dark:hover:bg-white/20 transition-colors"
          >
            ✏️ {t('approval.revise')}
          </button>
          <button
            onClick={() => onApprove(t('approval.approve'))}
            className="rounded-full bg-green-600 px-6 py-2 text-sm font-semibold text-white shadow-lg shadow-green-600/25 hover:bg-green-700 ring-2 ring-green-600/20 ring-offset-2 dark:ring-offset-gray-900 transition-all"
            autoFocus
          >
            ✅ {t('approval.approve')}
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <textarea
            value={revision}
            onChange={e => setRevision(e.target.value)}
            placeholder={t('approval.prompt')}
            className="w-full rounded-xl border border-[var(--panel-border)] bg-[var(--surface)] px-4 py-3 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-amber-400"
            rows={3}
            autoFocus
          />
          <div className="flex gap-2">
            <button onClick={() => setMode('action')} className="rounded-full border border-[var(--panel-border)] px-4 py-2 text-sm text-[var(--text-secondary)]">
              {t('approval.back')}
            </button>
            <button
              onClick={() => { onApprove(revision.trim()); setRevision(''); setMode('action') }}
              disabled={!revision.trim()}
              className="rounded-full bg-amber-500 px-5 py-2 text-sm font-medium text-white disabled:opacity-50 hover:bg-amber-600 transition-colors"
            >
              {t('input.send')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
