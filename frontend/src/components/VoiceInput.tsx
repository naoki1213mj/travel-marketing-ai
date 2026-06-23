/**
 * 音声入力コンポーネント。
 * Voice Live API が利用可能な場合は WebSocket で接続、
 * 利用不可の場合は Web Speech API にフォールバック。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiUrl } from '../lib/api-base'
import { AzureSpeechRecognizer } from '../lib/speech-stt'

interface VoiceInputProps {
  onTranscript: (text: string) => void
  disabled?: boolean
  voiceLiveAvailable?: boolean
  voiceTalkToStartAvailable?: boolean
  t: (key: string) => string
}

type VoiceState = 'idle' | 'connecting' | 'listening' | 'processing' | 'speaking' | 'error'

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

function appendTranscript(current: string, next: string): string {
  const trimmedNext = next.trim()
  if (!trimmedNext) return current
  return current ? `${current} ${trimmedNext}` : trimmedNext
}

function buildTranscriptDraft(finalText: string, interimText: string): string {
  const finalDraft = finalText.trim()
  const interimDraft = interimText.trim()
  return [finalDraft, interimDraft].filter(Boolean).join(' ')
}

export function VoiceInput({
  onTranscript,
  disabled = false,
  voiceLiveAvailable,
  voiceTalkToStartAvailable,
  t,
}: VoiceInputProps) {
  const [state, setState] = useState<VoiceState>('idle')
  const [transcript, setTranscript] = useState('')
  const [useAzureSpeech, setUseAzureSpeech] = useState<boolean | null>(null)
  const clientRef = useRef<AzureSpeechRecognizer | null>(null)
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null)
  const activeSessionIdRef = useRef(0)
  const idleResetTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const finalTranscriptRef = useRef('')
  const azureInterimRef = useRef('')

  const clearIdleResetTimeout = useCallback(() => {
    if (idleResetTimeoutRef.current) {
      clearTimeout(idleResetTimeoutRef.current)
      idleResetTimeoutRef.current = null
    }
  }, [])

  const beginSession = useCallback(() => {
    clearIdleResetTimeout()
    const nextSessionId = activeSessionIdRef.current + 1
    activeSessionIdRef.current = nextSessionId
    finalTranscriptRef.current = ''
    azureInterimRef.current = ''
    setTranscript('')
    return nextSessionId
  }, [clearIdleResetTimeout])

  const isActiveSession = useCallback((sessionId: number) => activeSessionIdRef.current === sessionId, [])

  const publishTranscript = useCallback((finalText: string, interimText = '') => {
    const draft = buildTranscriptDraft(finalText, interimText)
    setTranscript(draft)
    if (draft) {
      onTranscript(draft)
    }
  }, [onTranscript])

  const scheduleIdleReset = useCallback((sessionId: number) => {
    clearIdleResetTimeout()
    idleResetTimeoutRef.current = setTimeout(() => {
      if (!isActiveSession(sessionId)) {
        return
      }
      idleResetTimeoutRef.current = null
      setState('idle')
    }, 3000)
  }, [clearIdleResetTimeout, isActiveSession])

  // Azure Speech 利用可能性チェック — backend の音声設定を取得する。
  // setState は async コールバック内だけで呼び、エフェクト本体での同期 setState を避ける。
  useEffect(() => {
    let cancelled = false

    const resolveAvailability = async (): Promise<boolean> => {
      if (voiceTalkToStartAvailable === false || voiceLiveAvailable === false) {
        return false
      }
      // 以前に失敗している場合は Web Speech にフォールバックしたまま再試行しない
      if (sessionStorage.getItem('azureSpeechFailed') === 'true') {
        return false
      }
      try {
        const data = (await fetch(apiUrl('/api/speech-config')).then(r => r.json())) as {
          mode?: string
          available?: boolean
        }
        return data.available === true || data.mode === 'azure_speech'
      } catch {
        return false
      }
    }

    void resolveAvailability().then(result => {
      if (!cancelled) {
        setUseAzureSpeech(result)
      }
    })

    return () => {
      cancelled = true
    }
  }, [voiceLiveAvailable, voiceTalkToStartAvailable])

  const startWebSpeech = useCallback(() => {
    const SpeechRecognitionClass = getSpeechRecognition()
    if (!SpeechRecognitionClass) {
      setState('error')
      return
    }

    const sessionId = beginSession()
    const recognition = new SpeechRecognitionClass()
    recognition.lang = 'ja-JP'
    recognition.continuous = true
    recognition.interimResults = true

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      if (!isActiveSession(sessionId) || recognitionRef.current !== recognition) {
        return
      }
      let interim = ''
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) {
          finalTranscriptRef.current = appendTranscript(
            finalTranscriptRef.current,
            event.results[i][0].transcript,
          )
        } else {
          interim = appendTranscript(interim, event.results[i][0].transcript)
        }
      }
      publishTranscript(finalTranscriptRef.current, interim)
    }
    recognition.onerror = (e: { error: string }) => {
      if (!isActiveSession(sessionId) || recognitionRef.current !== recognition) {
        return
      }
      console.warn('Web Speech error:', e.error)
      recognitionRef.current = null
      setState('idle')
    }
    recognition.onend = () => {
      if (!isActiveSession(sessionId) || recognitionRef.current !== recognition) {
        return
      }
      recognitionRef.current = null
      setState('idle')
    }

    recognition.start()
    recognitionRef.current = recognition
    setState('listening')
  }, [beginSession, isActiveSession, publishTranscript])

  const startAzureSpeech = useCallback(async () => {
    const sessionId = beginSession()
    setState('connecting')
    try {
      const recognizer = new AzureSpeechRecognizer(apiUrl('/api/speech-token'), {
        onTranscript: (text, isFinal) => {
          if (!isActiveSession(sessionId)) {
            return
          }
          if (isFinal) {
            finalTranscriptRef.current = appendTranscript(finalTranscriptRef.current, text)
            azureInterimRef.current = ''
            publishTranscript(finalTranscriptRef.current)
            return
          }
          // Azure Speech の recognizing は現在の発話の全文を返すため、追記せず置換する。
          azureInterimRef.current = text.trim()
          publishTranscript(finalTranscriptRef.current, azureInterimRef.current)
        },
        onError: (error) => {
          if (!isActiveSession(sessionId)) {
            return
          }
          console.warn('Azure Speech error:', error)
          setState('error')
          scheduleIdleReset(sessionId)
        },
        onStateChange: (s) => {
          if (!isActiveSession(sessionId)) {
            return
          }
          if (s === 'listening') setState('listening')
          else if (s === 'connecting') setState('connecting')
          else if (s === 'stopped') setState('idle')
        },
      })

      await recognizer.start()
      if (!isActiveSession(sessionId)) {
        recognizer.stop()
        return
      }
      clientRef.current = recognizer
    } catch (err) {
      if (!isActiveSession(sessionId)) {
        return
      }
      console.warn('Azure Speech 接続失敗、Web Speech API にフォールバック:', err)
      sessionStorage.setItem('azureSpeechFailed', 'true')
      setState('idle')
      setUseAzureSpeech(false)
      startWebSpeech()
    }
  }, [beginSession, isActiveSession, publishTranscript, scheduleIdleReset, startWebSpeech])

  const stop = useCallback(() => {
    activeSessionIdRef.current += 1
    clearIdleResetTimeout()
    clientRef.current?.stop()
    clientRef.current = null
    if (recognitionRef.current) {
      recognitionRef.current.stop()
      recognitionRef.current = null
    }
    setState('idle')
  }, [clearIdleResetTimeout])

  const toggle = useCallback(() => {
    if (state !== 'idle') {
      stop()
    } else if (useAzureSpeech) {
      startAzureSpeech()
    } else {
      startWebSpeech()
    }
  }, [state, useAzureSpeech, startAzureSpeech, startWebSpeech, stop])

  // アンマウント時にクリーンアップ
  useEffect(() => () => stop(), [stop])

  const isVoiceDisabled = disabled || voiceTalkToStartAvailable === false
  const isActive = state !== 'idle' && state !== 'error'
  const buttonLabel = isActive ? t('voice.stop') : t('voice.talk_to_start')
  const stateLabel = state === 'listening' ? t('voice.listening')
    : state === 'processing' ? t('voice.processing')
    : state === 'speaking' ? t('voice.speaking')
    : state === 'connecting' ? t('voice.connecting')
    : voiceTalkToStartAvailable === false ? t('voice.unavailable')
    : state === 'error' ? t('voice.unsupported')
    : ''

  return (
    <div className="flex flex-col items-start gap-1">
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={toggle}
          disabled={isVoiceDisabled || useAzureSpeech === null}
          className={`inline-flex items-center justify-center gap-2 rounded-full border px-3 py-2.5 text-xs font-medium transition-all ${
            state === 'listening'
              ? 'animate-pulse border-red-400 bg-red-50 text-red-500 dark:bg-red-900/30 dark:text-red-400'
              : state === 'processing'
                ? 'border-yellow-400 bg-yellow-50 text-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-400'
                : state === 'speaking'
                  ? 'border-blue-400 bg-blue-50 text-blue-500 dark:bg-blue-900/30 dark:text-blue-400'
                  : 'border-[var(--panel-border)] bg-[var(--panel-strong)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
          } ${isVoiceDisabled ? 'cursor-not-allowed opacity-50' : ''}`}
          aria-label={buttonLabel}
          title={useAzureSpeech ? t('voice.provider') : t('voice.talk_to_start')}
        >
          {isActive ? (
            <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="2" />
            </svg>
          ) : (
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8} aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
            </svg>
          )}
          <span>{buttonLabel}</span>
        </button>
        {stateLabel && (
          <span className={`max-w-56 truncate rounded-full px-3 py-1 text-xs ${
            state === 'listening'
              ? 'bg-red-50 text-red-600 dark:bg-red-900/30 dark:text-red-400'
              : state === 'processing'
                ? 'bg-yellow-50 text-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-400'
                : state === 'speaking'
                  ? 'bg-blue-50 text-blue-500 dark:bg-blue-900/30 dark:text-blue-400'
                  : 'text-[var(--text-muted)]'
          }`}>
            {stateLabel}
          </span>
        )}
        {useAzureSpeech && state === 'idle' && (
          <span className="text-[10px] text-[var(--success-text)]">{t('voice.provider')}</span>
        )}
      </div>
      {transcript && (
        <div
          className="max-w-[280px] rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-2 text-xs text-[var(--text-secondary)]"
          role="status"
          aria-live="polite"
        >
          <p className="font-medium text-[var(--text-primary)]">{t('voice.review_hint')}</p>
          <p className="mt-1 line-clamp-2 whitespace-pre-wrap">{transcript}</p>
        </div>
      )}
    </div>
  )
}
