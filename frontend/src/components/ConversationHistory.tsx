/**
 * 会話履歴パネル。左パネル上部にインラインで表示する折りたたみ式。
 */

import { useCallback, useEffect, useState } from 'react'

interface Conversation {
  id: string
  input: string
  status: string
  created_at: string
}

interface ConversationHistoryProps {
  onSelect: (conversationId: string) => void
  t: (key: string) => string
  locale: string
}

export function ConversationHistory({ onSelect, t, locale }: ConversationHistoryProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [loading, setLoading] = useState(false)

  const fetchHistory = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetch('/api/conversations')
      const data = await resp.json()
      setConversations(data.conversations || [])
    } catch {
      setConversations([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isOpen) fetchHistory()
  }, [isOpen, fetchHistory])

  const formatTime = (dateStr: string) => {
    try {
      const d = new Date(dateStr)
      const now = new Date()
      const mins = Math.floor((now.getTime() - d.getTime()) / 60000)
      if (mins < 60) return `${mins}m`
      const hrs = Math.floor(mins / 60)
      if (hrs < 24) return `${hrs}h`
      return d.toLocaleDateString(locale, { month: 'short', day: 'numeric' })
    } catch { return '' }
  }

  const statusColor = (status: string) => {
    switch (status) {
      case 'completed': return 'bg-green-500'
      case 'awaiting_approval': return 'bg-amber-500'
      case 'error': return 'bg-red-500'
      default: return 'bg-blue-500'
    }
  }

  if (!isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-1.5 rounded-full border border-[var(--panel-border)] bg-[var(--surface)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--accent-soft)] hover:text-[var(--text-primary)]"
        title={t('history.title')}
      >
        <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        {t('history.title')}
      </button>
    )
  }

  return (
    <div className="rounded-2xl border border-[var(--panel-border)] bg-[var(--surface)] overflow-hidden">
      {/* ヘッダー */}
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-[var(--panel-border)]">
        <div className="flex items-center gap-2">
          <svg className="h-4 w-4 text-[var(--accent)]" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span className="text-xs font-semibold">{t('history.title')}</span>
          <span className="rounded-full bg-[var(--accent-soft)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent-strong)]">
            {conversations.length}
          </span>
        </div>
        <button
          onClick={() => setIsOpen(false)}
          aria-label={t('history.close')}
          title={t('history.close')}
          className="rounded-md p-1 text-[var(--text-muted)] hover:bg-[var(--accent-soft)] transition-colors"
        >
          <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* コンテンツ */}
      <div className="px-3 py-2.5">
        {loading ? (
          <div className="flex justify-center py-4">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
          </div>
        ) : conversations.length === 0 ? (
          <p className="py-3 text-center text-xs text-[var(--text-muted)]">{t('history.empty')}</p>
        ) : (
          <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-thin">
            {conversations.map((conv) => (
              <button
                key={conv.id}
                onClick={() => { onSelect(conv.id); setIsOpen(false) }}
                className="group flex-shrink-0 w-48 rounded-xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-3 text-left transition-all hover:border-[var(--accent)] hover:shadow-sm"
              >
                <div className="flex items-start gap-2">
                  <span className={`mt-1 h-2 w-2 flex-shrink-0 rounded-full ${statusColor(conv.status)}`} />
                  <p className="flex-1 text-xs leading-snug line-clamp-2 group-hover:text-[var(--accent-strong)]">
                    {conv.input || t('history.no_input')}
                  </p>
                </div>
                <p className="mt-1.5 text-[10px] text-[var(--text-muted)] pl-4">
                  {formatTime(conv.created_at)}
                </p>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
