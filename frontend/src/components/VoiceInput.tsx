import { useCallback, useEffect, useRef, useState } from 'react'

interface VoiceInputProps {
  onTranscript: (text: string) => void
  disabled?: boolean
  t: (key: string) => string
}

type VoiceState = 'idle' | 'listening' | 'processing'

// Web Speech API の型定義（ブラウザ互換）
interface SpeechRecognitionEvent {
  readonly resultIndex: number
  readonly results: SpeechRecognitionResultList
}

interface SpeechRecognitionResultList {
  readonly length: number
  item(index: number): SpeechRecognitionResult
  [index: number]: SpeechRecognitionResult
}

interface SpeechRecognitionResult {
  readonly isFinal: boolean
  readonly length: number
  item(index: number): SpeechRecognitionAlternative
  [index: number]: SpeechRecognitionAlternative
}

interface SpeechRecognitionAlternative {
  readonly transcript: string
  readonly confidence: number
}

interface SpeechRecognitionInstance extends EventTarget {
  continuous: boolean
  interimResults: boolean
  lang: string
  onresult: ((event: SpeechRecognitionEvent) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void
  stop(): void
  abort(): void
}

interface SpeechRecognitionConstructor {
  new (): SpeechRecognitionInstance
}

function getSpeechRecognition(): SpeechRecognitionConstructor | null {
  const w = window as unknown as Record<string, unknown>
  return (w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null) as SpeechRecognitionConstructor | null
}

export function VoiceInput({ onTranscript, disabled = false, t }: VoiceInputProps) {
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [isSupported] = useState(() => getSpeechRecognition() !== null)
  const [interimText, setInterimText] = useState('')
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null)

  // アンマウント時にクリーンアップ
  useEffect(() => {
    return () => {
      if (recognitionRef.current) {
        recognitionRef.current.onresult = null
        recognitionRef.current.onerror = null
        recognitionRef.current.onend = null
        recognitionRef.current.abort()
        recognitionRef.current = null
      }
    }
  }, [])

  const startListening = useCallback(() => {
    const SpeechRecognitionClass = getSpeechRecognition()
    if (!SpeechRecognitionClass) return

    const recognition = new SpeechRecognitionClass()
    recognition.continuous = false
    recognition.interimResults = true
    recognition.lang = 'ja-JP'

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let final_transcript = ''
      let interim_transcript = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i]
        if (result.isFinal) {
          final_transcript += result[0].transcript
        } else {
          interim_transcript += result[0].transcript
        }
      }

      if (final_transcript) {
        setVoiceState('processing')
        setInterimText('')
        onTranscript(final_transcript)
        setTimeout(() => setVoiceState('idle'), 500)
      } else if (interim_transcript) {
        setInterimText(interim_transcript)
      }
    }

    recognition.onerror = () => {
      setVoiceState('idle')
      setInterimText('')
    }

    recognition.onend = () => {
      setVoiceState('idle')
      recognitionRef.current = null
    }

    recognitionRef.current = recognition
    setVoiceState('listening')
    recognition.start()
  }, [onTranscript])

  const stopListening = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.stop()
    }
    setVoiceState('idle')
    setInterimText('')
  }, [])

  const toggleVoice = useCallback(() => {
    if (voiceState === 'listening') {
      stopListening()
    } else {
      startListening()
    }
  }, [voiceState, startListening, stopListening])

  // ブラウザが Web Speech API をサポートしていない場合
  if (!isSupported) {
    return (
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled
          className="inline-flex cursor-not-allowed items-center justify-center rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] p-2.5 text-[var(--text-secondary)] opacity-50"
          aria-label={t('voice.button')}
          title={t('voice.preview')}
        >
          <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
          </svg>
        </button>
        <span className="max-w-56 rounded-full bg-[var(--accent-soft)] px-3 py-1 text-xs text-[var(--accent-strong)]">
          {t('voice.unsupported')}
        </span>
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={toggleVoice}
        disabled={disabled || voiceState === 'processing'}
        className={`inline-flex items-center justify-center rounded-full border p-2.5 transition-all ${
          voiceState === 'listening'
            ? 'animate-pulse border-red-400 bg-red-50 text-red-500 dark:bg-red-900/30 dark:text-red-400'
            : voiceState === 'processing'
              ? 'border-yellow-400 bg-yellow-50 text-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-400'
              : 'border-[var(--panel-border)] bg-[var(--panel-strong)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
        } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
        aria-label={voiceState === 'listening' ? t('voice.stop') : t('voice.button')}
        title={voiceState === 'listening' ? t('voice.stop') : t('voice.label')}
      >
        {voiceState === 'listening' ? (
          <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
        ) : (
          <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
          </svg>
        )}
      </button>
      {voiceState === 'listening' && (
        <span className="max-w-56 truncate rounded-full bg-red-50 px-3 py-1 text-xs text-red-600 dark:bg-red-900/30 dark:text-red-400">
          {interimText || t('voice.listening')}
        </span>
      )}
      {voiceState === 'processing' && (
        <span className="rounded-full bg-yellow-50 px-3 py-1 text-xs text-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-400">
          {t('voice.processing')}
        </span>
      )}
    </div>
  )
}
