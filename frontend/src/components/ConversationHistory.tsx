import { useCallback, useState } from 'react'

interface Conversation {
  id: string
  input: string
  status: string
  created_at: string
}

interface ConversationHistoryProps {
  onSelect: (conversationId: string) => void
  t: (key: string) => string
}

export function ConversationHistory({ onSelect, t }: ConversationHistoryProps) {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [isOpen, setIsOpen] = useState(false)

  const loadConversations = useCallback(async () => {
    try {
      const res = await fetch('/api/conversations')
      if (res.ok) {
        const data = await res.json()
        setConversations(data.conversations || [])
      }
    } catch {
      // サイレントに無視
    }
  }, [])

  const toggle = useCallback(() => {
    const next = !isOpen
    setIsOpen(next)
    if (next) {
      loadConversations()
    }
  }, [isOpen, loadConversations])

  return (
    <div className="relative">
      <button
        type="button"
        onClick={toggle}
        className="inline-flex items-center gap-2 rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-2 text-xs text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
        title={t('history.title')}
      >
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        {t('history.title')}
      </button>

      {isOpen && (
        <>
          {/* 背景オーバーレイ（クリックで閉じる） */}
          <div
            className="fixed inset-0 z-[60] bg-black/20 backdrop-blur-sm"
            onClick={() => setIsOpen(false)}
            aria-hidden="true"
          />
          <div className="fixed right-4 top-20 z-[70] w-80 rounded-[20px] border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4 shadow-2xl sm:absolute sm:right-0 sm:top-full sm:mt-2 sm:fixed-auto"
               style={{ background: 'var(--panel-bg)' }}>
            <h3 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">{t('history.title')}</h3>
            {conversations.length === 0 ? (
              <p className="text-xs text-[var(--text-muted)]">{t('history.empty')}</p>
            ) : (
              <ul className="max-h-64 space-y-2 overflow-y-auto">
                {conversations.map((conv) => (
                  <li key={conv.id}>
                    <button
                      type="button"
                      onClick={() => {
                        onSelect(conv.id)
                        setIsOpen(false)
                      }}
                      className="w-full rounded-xl border border-[var(--panel-border)] bg-[var(--panel-bg)] px-3 py-2 text-left transition-colors hover:bg-[var(--accent-soft)]"
                    >
                      <p className="truncate text-xs font-medium text-[var(--text-primary)]">
                        {conv.input || conv.id}
                      </p>
                      <p className="mt-1 text-[10px] text-[var(--text-muted)]">
                        {conv.status} · {new Date(conv.created_at).toLocaleString()}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  )
}
