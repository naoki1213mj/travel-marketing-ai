import { useState, type FormEvent } from 'react'

const MAX_LENGTH = 5000
const WARN_THRESHOLD = 4500

interface InputFormProps {
  onSubmit: (message: string) => void
  disabled: boolean
  placeholder: string
  sendLabel: string
  label: string
  initialValue?: string
}

export function InputForm({ onSubmit, disabled, placeholder, sendLabel, label, initialValue }: InputFormProps) {
  // initialValue が変わったら message にセットする（React 19 の key パターン）
  const [prevInitial, setPrevInitial] = useState('')
  const [message, setMessage] = useState('')

  if (initialValue && initialValue !== prevInitial) {
    setPrevInitial(initialValue)
    setMessage(initialValue)
  }

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
    <form onSubmit={handleSubmit} className="flex flex-col gap-1">
      <div className="flex gap-3">
        <label className="sr-only" htmlFor="input-form-message">{label}</label>
        <textarea
          id="input-form-message"
          value={message}
          onChange={e => setMessage(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          rows={3}
          maxLength={MAX_LENGTH + 100}
          aria-label={label}
          className="flex-1 resize-none rounded-[24px] border-2 border-[var(--input-border)] bg-[var(--input-bg)] px-4 py-3
                     text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)]
                     focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-soft)]
                     disabled:opacity-50
          "
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSubmit(e)
            }
          }}
        />
        <button
          type="submit"
          disabled={!canSubmit}
          className="self-end rounded-full bg-[var(--accent-strong)] px-6 py-3 text-sm font-medium text-white
                     dark:bg-teal-700 dark:text-white
                     hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-[var(--accent-soft)]
                     disabled:opacity-40 disabled:cursor-not-allowed
          "
        >
          {sendLabel}
        </button>
      </div>
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
    </form>
  )
}
