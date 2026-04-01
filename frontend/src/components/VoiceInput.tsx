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

export function VoiceInput({ onTranscript, disabled = false, t }: VoiceInputProps) {
  const [state, setState] = useState<VoiceState>('idle')
  const [transcript, setTranscript] = useState('')
  const [useVoiceLive, setUseVoiceLive] = useState<boolean | null>(null)
  const clientRef = useRef<VoiceLiveClient | null>(null)
  const recognitionRef = useRef<SpeechRecognitionInstance | null>(null)

  // Voice Live 利用可能性チェック — MSAL.js トークン取得を試みる
  useEffect(() => {
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
  }, [])

  const startWebSpeech = useCallback(() => {
    const SpeechRecognitionClass = getSpeechRecognition()
    if (!SpeechRecognitionClass) {
      setState('error')
      return
    }
    const recognition = new SpeechRecognitionClass()
    recognition.lang = 'ja-JP'
    recognition.continuous = false
    recognition.interimResults = true

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let finalText = ''
      let interim = ''
      for (let i = 0; i < event.results.length; i++) {
        if (event.results[i].isFinal) {
          finalText += event.results[i][0].transcript
        } else {
          interim += event.results[i][0].transcript
        }
      }
      if (finalText) {
        onTranscript(finalText)
        setTranscript('')
      } else {
        setTranscript(interim)
      }
    }
    recognition.onerror = (e: { error: string }) => {
      console.warn('Web Speech error:', e.error)
      setState('idle')
    }
    recognition.onend = () => {
      setState('idle')
    }

    recognition.start()
    recognitionRef.current = recognition
    setState('listening')
  }, [onTranscript])

  const startVoiceLive = useCallback(async () => {
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
          setTranscript(text)
          if (isFinal) {
            onTranscript(text)
            setTranscript('')
          }
        },
        onAgentText: () => {
          // エージェント応答テキスト — 将来の UI 表示用
        },
        onError: (error) => {
          console.warn('Voice Live error:', error)
          setState('error')
          // 3秒後にアイドルに戻す
          setTimeout(() => setState('idle'), 3000)
        },
        onStateChange: (s) => {
          if (s === 'listening') setState('listening')
          else if (s === 'processing') setState('processing')
          else if (s === 'speaking') setState('speaking')
          else if (s === 'connected') setState('listening')
          else if (s === 'disconnected') setState('idle')
          else if (s === 'connecting') setState('connecting')
        },
      })

      await client.connect()
      clientRef.current = client
    } catch (err) {
      console.warn('Voice Live 接続失敗、Web Speech API にフォールバック:', err)
      sessionStorage.setItem('voiceLiveFailed', 'true')
      setState('idle')
      setUseVoiceLive(false)
      startWebSpeech()
    }
  }, [onTranscript, startWebSpeech])

  const stop = useCallback(() => {
    clientRef.current?.disconnect()
    clientRef.current = null
    if (recognitionRef.current) {
      recognitionRef.current.stop()
      recognitionRef.current = null
    }
    setState('idle')
    setTranscript('')
  }, [])

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

  const isActive = state !== 'idle' && state !== 'error'
  const stateLabel = state === 'listening' ? t('voice.listening')
    : state === 'processing' ? t('voice.processing')
    : state === 'speaking' ? t('voice.speaking')
    : state === 'connecting' ? t('voice.connecting')
    : state === 'error' ? t('voice.unsupported')
    : ''

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={toggle}
        disabled={disabled || useVoiceLive === null}
        className={`inline-flex items-center justify-center rounded-full border p-2.5 transition-all ${
          state === 'listening'
            ? 'animate-pulse border-red-400 bg-red-50 text-red-500 dark:bg-red-900/30 dark:text-red-400'
            : state === 'processing'
              ? 'border-yellow-400 bg-yellow-50 text-yellow-600 dark:bg-yellow-900/30 dark:text-yellow-400'
              : state === 'speaking'
                ? 'border-blue-400 bg-blue-50 text-blue-500 dark:bg-blue-900/30 dark:text-blue-400'
                : 'border-[var(--panel-border)] bg-[var(--panel-strong)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
        } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
        aria-label={t('voice.label')}
        title={useVoiceLive ? t('voice.provider') : t('voice.label')}
      >
        {isActive ? (
          <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 24 24">
            <rect x="6" y="6" width="12" height="12" rx="2" />
          </svg>
        ) : (
          <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
          </svg>
        )}
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
      {transcript && (
        <span className="max-w-[200px] truncate text-xs text-[var(--text-secondary)]">
          {transcript}
        </span>
      )}
      {useVoiceLive && state === 'idle' && (
        <span className="text-[10px] text-[var(--success-text)]">{t('voice.provider')}</span>
      )}
    </div>
  )
}
