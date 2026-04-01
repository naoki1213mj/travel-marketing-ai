import { Check, MessageSquareWarning, Pencil } from 'lucide-react';
import { useState } from 'react';

interface ApprovalBannerProps {
  request: { prompt: string; plan_markdown?: string; conversation_id: string }
  onApprove: (response: string) => void
  t: (key: string) => string
}

export function ApprovalBanner({ request, onApprove, t }: ApprovalBannerProps) {
  const [mode, setMode] = useState<'action' | 'revise'>('action')
  const [revision, setRevision] = useState('')
  const displayPrompt = request.prompt.trim() && !/承認|修正|approve|revise/i.test(request.prompt)
    ? request.prompt
    : t('approval.message')

  return (
    <div className="sticky bottom-0 z-30 mx-0 mt-2 rounded-2xl border border-amber-300 bg-white/95 dark:bg-slate-900/95 px-6 py-5 shadow-[0_-4px_30px_rgba(251,191,36,0.15)] backdrop-blur-lg">
        <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-amber-800 dark:text-amber-200 mb-3">
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
          </span>
          <MessageSquareWarning className="h-4 w-4" /> {t('approval.title')}
        </h3>

      {mode === 'action' ? (
        <div className="flex items-center gap-3">
          <p className="flex-1 text-sm text-[var(--text-secondary)]">{displayPrompt}</p>
          <button
            onClick={() => setMode('revise')}
            className="inline-flex items-center gap-1.5 rounded-full border border-[var(--panel-border)] bg-white/80 dark:bg-white/10 px-4 py-2 text-sm font-medium text-[var(--text-secondary)] hover:bg-white dark:hover:bg-white/20 transition-colors"
          >
            <Pencil className="h-3.5 w-3.5" /> {t('approval.revise')}
          </button>
          <button
            onClick={() => onApprove(t('approval.approve'))}
            className="inline-flex items-center gap-1.5 rounded-full bg-green-600 px-6 py-2 text-sm font-semibold text-white shadow-lg shadow-green-600/25 hover:bg-green-700 dark:bg-green-700 dark:hover:bg-green-800 ring-2 ring-green-600/20 ring-offset-2 dark:ring-offset-gray-900 transition-all"
            autoFocus
          >
            <Check className="h-4 w-4" /> {t('approval.approve')}
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
