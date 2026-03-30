import { useState, type FormEvent } from 'react'

const MAX_LENGTH = 5000
const WARN_THRESHOLD = 4500

interface RefineChatProps {
  onSubmit: (message: string) => void
  disabled: boolean
  placeholder: string
  sendLabel: string
  label: string
}

export function RefineChat({ onSubmit, disabled, placeholder, sendLabel, label }: RefineChatProps) {
  const [message, setMessage] = useState('')

  const isOverLimit = message.length > MAX_LENGTH
  const isNearLimit = message.length > WARN_THRESHOLD
  const canSubmit = !disabled && message.trim().length > 0 && !isOverLimit

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!canSubmit) return
    onSubmit(message.trim())
    setMessage('')
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-1 pt-3 border-t border-[var(--panel-border)]">
      <div className="flex gap-2">
        <label className="sr-only" htmlFor="refine-chat-message">{label}</label>
        <input
          id="refine-chat-message"
          type="text"
          value={message}
          onChange={e => setMessage(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          maxLength={MAX_LENGTH + 100}
          aria-label={label}
          className="flex-1 rounded-full border-2 border-[var(--input-border)] bg-[var(--input-bg)] px-4 py-2.5 text-sm text-[var(--text-primary)]
                     placeholder:text-[var(--text-muted)] focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-soft)]
                     disabled:opacity-50
          "
        />
        <button
          type="submit"
          disabled={!canSubmit}
          className="rounded-full bg-[var(--accent-strong)] px-4 py-2 text-sm text-white
                     hover:opacity-90 disabled:opacity-40"
        >
          {sendLabel}
        </button>
      </div>
      {message.length > 0 && (
        <span
          className={`self-end text-xs pr-1 ${
            isOverLimit
              ? 'text-red-500 font-semibold'
              : isNearLimit
                ? 'text-red-400'
                : 'text-[var(--text-muted)]'
          }`}
        >
          {message.length} / {MAX_LENGTH}
        </span>
      )}
    </form>
  )
}
