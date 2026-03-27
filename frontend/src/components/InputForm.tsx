import { useState, type FormEvent } from 'react'

interface InputFormProps {
  onSubmit: (message: string) => void
  disabled: boolean
  placeholder: string
  sendLabel: string
}

export function InputForm({ onSubmit, disabled, placeholder, sendLabel }: InputFormProps) {
  const [message, setMessage] = useState('')

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!message.trim() || disabled) return
    onSubmit(message.trim())
    setMessage('')
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-3">
      <textarea
        value={message}
        onChange={e => setMessage(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        rows={3}
        className="flex-1 resize-none rounded-lg border border-gray-200 bg-white px-4 py-3
                   text-sm text-gray-900 placeholder-gray-400
                   focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100
                   disabled:opacity-50
                   dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100
                   dark:placeholder-gray-500 dark:focus:border-blue-500 dark:focus:ring-blue-900"
        onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            handleSubmit(e)
          }
        }}
      />
      <button
        type="submit"
        disabled={disabled || !message.trim()}
        className="self-end rounded-lg bg-blue-600 px-6 py-3 text-sm font-medium text-white
                   hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-300
                   disabled:opacity-40 disabled:cursor-not-allowed
                   dark:bg-blue-500 dark:hover:bg-blue-600"
      >
        {sendLabel}
      </button>
    </form>
  )
}
