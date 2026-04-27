/**
 * 音声入力コンポーネント。
 * Voice Live API が利用可能な場合は WebSocket で接続、
 * 利用不可の場合は Web Speech API にフォールバック。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { getVoiceLiveToken } from '../lib/msal-auth'
import { VoiceLiveClient, type VoiceLiveConfig } from '../lib/voice-live'

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
  const [useVoiceLive, setUseVoiceLive] = useState<boolean | null>(null)
  const clientRef = useRef<VoiceLiveClient | null>(null)
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null)
  const activeSessionIdRef = useRef(0)
  const idleResetTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const finalTranscriptRef = useRef('')
  const voiceLiveInterimRef = useRef('')

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
    voiceLiveInterimRef.current = ''
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

  // Voice Live 利用可能性チェック — MSAL.js トークン取得を試みる
  useEffect(() => {
    if (voiceTalkToStartAvailable === false) {
      setUseVoiceLive(false)
      return
    }

    if (voiceLiveAvailable === false) {
      setUseVoiceLive(false)
      return
    }

    // Voice Live が以前失敗している場合はスキップ（Edge リダイレクト後の再試行防止）
    if (sessionStorage.getItem('voiceLiveFailed') === 'true') {
      setUseVoiceLive(false)
      return
    }

    fetch('/api/voice-config')
      .then(r => r.json())
      .then((data: { agent_name?: string; client_id?: string }) => {
        setUseVoiceLive(!!data.client_id)
      })
      .catch(() => setUseVoiceLive(false))
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

  const startVoiceLive = useCallback(async () => {
    const sessionId = beginSession()
    setState('connecting')
    try {
      // Voice Live 設定を取得（client_id, tenant_id, endpoint 等）
      const configResp = await fetch('/api/voice-config')
      const configData = (await configResp.json()) as {
        client_id?: string; tenant_id?: string; endpoint?: string;
        agent_name?: string; project_name?: string; api_version?: string
      }

      if (!configData.client_id || !configData.endpoint) {
        throw new Error('Voice Live config missing client_id or endpoint')
      }

      // MSAL.js でユーザー委任トークンを取得
      const token = await getVoiceLiveToken({
        clientId: configData.client_id,
        tenantId: configData.tenant_id || '',
      })

      if (!token) {
        throw new Error('MSAL token acquisition failed')
      }

      const config: VoiceLiveConfig = {
        endpoint: configData.endpoint,
        token: token,
        agentName: configData.agent_name || 'travel-voice-orchestrator',
        projectName: configData.project_name || '',
        apiVersion: configData.api_version || '2026-01-01-preview',
      }

      const client = new VoiceLiveClient(config, {
        onTranscript: (text, isFinal) => {
          if (!isActiveSession(sessionId)) {
            return
          }
          if (isFinal) {
            finalTranscriptRef.current = appendTranscript(finalTranscriptRef.current, text)
            voiceLiveInterimRef.current = ''
            publishTranscript(finalTranscriptRef.current)
            return
          }
          voiceLiveInterimRef.current = appendTranscript(voiceLiveInterimRef.current, text)
          publishTranscript(finalTranscriptRef.current, voiceLiveInterimRef.current)
        },
        onAgentText: () => {
          // エージェント応答テキスト — 将来の UI 表示用
        },
        onError: (error) => {
          if (!isActiveSession(sessionId)) {
            return
          }
          console.warn('Voice Live error:', error)
          setState('error')
          scheduleIdleReset(sessionId)
        },
        onStateChange: (s) => {
          if (!isActiveSession(sessionId)) {
            return
          }
          if (s === 'listening') setState('listening')
          else if (s === 'processing') setState('processing')
          else if (s === 'speaking') setState('speaking')
          else if (s === 'connected') setState('listening')
          else if (s === 'disconnected') setState('idle')
          else if (s === 'connecting') setState('connecting')
        },
      })

      await client.connect()
      if (!isActiveSession(sessionId)) {
        client.disconnect()
        return
      }
      clientRef.current = client
    } catch (err) {
      if (!isActiveSession(sessionId)) {
        return
      }
      console.warn('Voice Live 接続失敗、Web Speech API にフォールバック:', err)
      sessionStorage.setItem('voiceLiveFailed', 'true')
      setState('idle')
      setUseVoiceLive(false)
      startWebSpeech()
    }
  }, [beginSession, isActiveSession, publishTranscript, scheduleIdleReset, startWebSpeech])

  const stop = useCallback(() => {
    activeSessionIdRef.current += 1
    clearIdleResetTimeout()
    clientRef.current?.disconnect()
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
    } else if (useVoiceLive) {
      startVoiceLive()
    } else {
      startWebSpeech()
    }
  }, [state, useVoiceLive, startVoiceLive, startWebSpeech, stop])

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
          disabled={isVoiceDisabled || useVoiceLive === null}
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
          title={useVoiceLive ? t('voice.provider') : t('voice.talk_to_start')}
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
        {useVoiceLive && state === 'idle' && (
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
