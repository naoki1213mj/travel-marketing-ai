import { SendHorizontal, Sparkles } from 'lucide-react'
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
  t: (key: string) => string
}

export function InputForm({
  onSubmit,
  disabled,
  placeholder,
  sendLabel,
  label,
  initialValue,
  t,
}: InputFormProps) {
  const [message, setMessage] = useState(initialValue ?? '')
  const quickChips = [
    { label: 'input.quick.okinawa.label', prompt: 'input.quick.okinawa.prompt' },
    { label: 'input.quick.hokkaido.label', prompt: 'input.quick.hokkaido.prompt' },
    { label: 'input.quick.kyoto.label', prompt: 'input.quick.kyoto.prompt' },
    { label: 'input.quick.hawaii.label', prompt: 'input.quick.hawaii.prompt' },
  ]

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
    <form
      onSubmit={handleSubmit}
      className="rounded-[28px] border border-[var(--input-border)] bg-[var(--surface)] p-3 shadow-[0_18px_45px_rgba(15,23,42,0.08)]"
    >
      {!disabled && !message && (
        <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center">
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-[var(--accent-strong)]">
            <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
            {t('input.quick.label')}
          </span>
          <div className="flex flex-wrap gap-1.5">
            {quickChips.map(chip => (
              <button
                key={chip.label}
                type="button"
                onClick={() => setMessage(t(chip.prompt))}
                className="rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-1.5 text-xs
                           font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:bg-[var(--accent-soft)] hover:text-[var(--accent-strong)]"
              >
                {t(chip.label)}
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-col gap-3">
        <label className="sr-only" htmlFor="input-form-message">{label}</label>
        <textarea
          id="input-form-message"
          value={message}
          onChange={e => setMessage(e.target.value)}
          placeholder={placeholder}
          disabled={disabled}
          rows={5}
          maxLength={MAX_LENGTH + 100}
          aria-label={label}
          className="min-h-32 w-full resize-y rounded-[22px] border border-[var(--input-border)] bg-[var(--input-bg)] px-4 py-4
                     text-base leading-7 text-[var(--text-primary)] shadow-inner shadow-slate-950/5 placeholder:text-[var(--text-muted)]
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
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[var(--text-muted)]">
            <span>{t('input.shortcuts')}</span>
            {message.length > 0 && (
              <span
                className={`${
                  isOverLimit
                    ? 'font-semibold text-red-500'
                    : isNearLimit
                      ? 'text-red-500'
                      : 'text-[var(--text-muted)]'
                }`}
              >
                {message.length} / {MAX_LENGTH}
              </span>
            )}
          </div>
          <button
            type="submit"
            disabled={!canSubmit}
            className="inline-flex min-h-12 items-center justify-center gap-2 rounded-full bg-[var(--accent-strong)] px-7 py-3 text-sm font-semibold text-white
                       shadow-[0_12px_30px_rgba(15,118,110,0.28)] transition hover:-translate-y-0.5 hover:opacity-95
                       dark:bg-teal-700 dark:text-white
                       focus:outline-none focus:ring-2 focus:ring-[var(--accent-soft)]
                       disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none disabled:hover:translate-y-0
            "
          >
            <SendHorizontal className="h-4 w-4" aria-hidden="true" />
            {sendLabel}
          </button>
        </div>
      </div>
    </form>
  )
}
