/**
 * Azure Speech Speech-to-Text クライアント。
 *
 * backend `/api/speech-token` から keyless（Managed Identity 発行）の短命 authorization token を
 * 取得し、Azure Speech SDK の継続認識で発話を文字起こしする。Speech SDK の実体は動的 import で
 * 遅延ロードし、メインバンドルを肥大化させない（音声を使うユーザーだけがロードする）。
 *
 * 旧 Voice Live（WebSocket 音声会話）方式とは異なり、MSAL ユーザーログインや voice agent は不要。
 */

import type * as SpeechSDK from 'microsoft-cognitiveservices-speech-sdk'

export type SpeechSttState = 'connecting' | 'listening' | 'stopped'

export interface SpeechSttHandlers {
  onTranscript: (text: string, isFinal: boolean) => void
  onError: (error: string) => void
  onStateChange: (state: SpeechSttState) => void
}

interface SpeechTokenResponse {
  token: string
  region: string
  language?: string
  expires_at?: number
}

// authorization token 失効の 60 秒前に更新する。最低でも 30 秒は待つ。
const TOKEN_REFRESH_LEAD_MS = 60_000
const TOKEN_REFRESH_MIN_MS = 30_000

export class AzureSpeechRecognizer {
  private readonly tokenUrl: string
  private readonly handlers: SpeechSttHandlers
  private recognizer: SpeechSDK.SpeechRecognizer | null = null
  private refreshTimer: ReturnType<typeof setTimeout> | null = null
  private stopped = false

  constructor(tokenUrl: string, handlers: SpeechSttHandlers) {
    this.tokenUrl = tokenUrl
    this.handlers = handlers
  }

  /** マイク認識を開始する。token 取得・接続に失敗した場合は例外を投げる（呼び出し側でフォールバック）。 */
  async start(): Promise<void> {
    this.handlers.onStateChange('connecting')
    const config = await this.fetchToken()
    const sdk = await import('microsoft-cognitiveservices-speech-sdk')

    const speechConfig = sdk.SpeechConfig.fromAuthorizationToken(config.token, config.region)
    speechConfig.speechRecognitionLanguage = config.language || 'ja-JP'
    const audioConfig = sdk.AudioConfig.fromDefaultMicrophoneInput()
    const recognizer = new sdk.SpeechRecognizer(speechConfig, audioConfig)

    recognizer.recognizing = (_sender, event) => {
      const text = event.result.text
      if (text) {
        this.handlers.onTranscript(text, false)
      }
    }
    recognizer.recognized = (_sender, event) => {
      if (event.result.reason === sdk.ResultReason.RecognizedSpeech && event.result.text) {
        this.handlers.onTranscript(event.result.text, true)
      }
    }
    recognizer.canceled = (_sender, event) => {
      if (event.reason === sdk.CancellationReason.Error) {
        this.handlers.onError(event.errorDetails || 'Speech recognition canceled')
      }
      this.stop()
    }
    recognizer.sessionStopped = () => {
      this.stop()
    }

    await new Promise<void>((resolve, reject) => {
      recognizer.startContinuousRecognitionAsync(
        () => resolve(),
        (err: string) => reject(new Error(err || 'startContinuousRecognition failed')),
      )
    })

    if (this.stopped) {
      // start() 完了前に stop() された場合は片付ける。
      this.disposeRecognizer(recognizer)
      return
    }

    this.recognizer = recognizer
    this.handlers.onStateChange('listening')
    this.scheduleTokenRefresh(config.expires_at)
  }

  /** 認識を停止し、リソースを解放する。冪等。 */
  stop(): void {
    if (this.stopped && !this.recognizer) {
      return
    }
    this.stopped = true
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer)
      this.refreshTimer = null
    }
    const recognizer = this.recognizer
    this.recognizer = null
    if (recognizer) {
      this.disposeRecognizer(recognizer)
    }
    this.handlers.onStateChange('stopped')
  }

  private disposeRecognizer(recognizer: SpeechSDK.SpeechRecognizer): void {
    try {
      recognizer.stopContinuousRecognitionAsync(
        () => recognizer.close(),
        () => recognizer.close(),
      )
    } catch {
      recognizer.close()
    }
  }

  private scheduleTokenRefresh(expiresAt?: number): void {
    if (!expiresAt) {
      return
    }
    const delay = Math.max(TOKEN_REFRESH_MIN_MS, expiresAt * 1000 - Date.now() - TOKEN_REFRESH_LEAD_MS)
    this.refreshTimer = setTimeout(() => {
      void this.refreshToken()
    }, delay)
  }

  private async refreshToken(): Promise<void> {
    if (this.stopped || !this.recognizer) {
      return
    }
    try {
      const next = await this.fetchToken()
      if (this.recognizer) {
        this.recognizer.authorizationToken = next.token
        this.scheduleTokenRefresh(next.expires_at)
      }
    } catch {
      // 更新に失敗してもセッションは token 失効まで継続する（その後 canceled で停止）。
    }
  }

  private async fetchToken(): Promise<SpeechTokenResponse> {
    const response = await fetch(this.tokenUrl, { headers: { Accept: 'application/json' } })
    if (!response.ok) {
      throw new Error(`speech-token request failed: HTTP ${response.status}`)
    }
    const data = (await response.json()) as SpeechTokenResponse
    if (!data.token || !data.region) {
      throw new Error('speech-token response missing token or region')
    }
    return data
  }
}
