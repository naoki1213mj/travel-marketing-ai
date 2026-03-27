import { useState, type FormEvent } from 'react'

interface RefineChatProps {
  onSubmit: (message: string) => void
  disabled: boolean
  placeholder: string
  sendLabel: string
}

export function RefineChat({ onSubmit, disabled, placeholder, sendLabel }: RefineChatProps) {
  const [message, setMessage] = useState('')

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!message.trim() || disabled) return
    onSubmit(message.trim())
    setMessage('')
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-2 pt-3 border-t border-gray-200 dark:border-gray-700">
      <input
        type="text"
        value={message}
        onChange={e => setMessage(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        className="flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm
                   placeholder-gray-400 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100
                   disabled:opacity-50
                   dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 dark:placeholder-gray-500"
      />
      <button
        type="submit"
        disabled={disabled || !message.trim()}
        className="rounded-lg bg-blue-600 px-4 py-2 text-sm text-white
                   hover:bg-blue-700 disabled:opacity-40
                   dark:bg-blue-500 dark:hover:bg-blue-600"
      >
        {sendLabel}
      </button>
    </form>
  )
}
