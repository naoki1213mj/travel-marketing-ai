import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AzureSpeechRecognizer } from './speech-stt'

const sdkState = vi.hoisted(() => ({
  recognizer: null as null | {
    recognizing: ((sender: unknown, event: { result: { text: string } }) => void) | null
    recognized: ((sender: unknown, event: { result: { reason: number; text: string } }) => void) | null
    canceled: ((sender: unknown, event: { reason: number; errorDetails: string }) => void) | null
    sessionStopped: (() => void) | null
    authorizationToken: string
    close: ReturnType<typeof vi.fn>
    startContinuousRecognitionAsync: ReturnType<typeof vi.fn>
    stopContinuousRecognitionAsync: ReturnType<typeof vi.fn>
    language: string
  },
  fromAuthCalls: [] as Array<[string, string]>,
  startShouldFail: false,
}))

vi.mock('microsoft-cognitiveservices-speech-sdk', () => {
  class SpeechConfig {
    speechRecognitionLanguage = ''
    static fromAuthorizationToken(token: string, region: string) {
      sdkState.fromAuthCalls.push([token, region])
      return new SpeechConfig()
    }
  }
  class AudioConfig {
    static fromDefaultMicrophoneInput() {
      return new AudioConfig()
    }
  }
  class SpeechRecognizer {
    recognizing: ((sender: unknown, event: { result: { text: string } }) => void) | null = null
    recognized: ((sender: unknown, event: { result: { reason: number; text: string } }) => void) | null = null
    canceled: ((sender: unknown, event: { reason: number; errorDetails: string }) => void) | null = null
    sessionStopped: (() => void) | null = null
    authorizationToken = ''
    close = vi.fn()
    startContinuousRecognitionAsync = vi.fn((onOk: () => void, onErr: (e: string) => void) => {
      if (sdkState.startShouldFail) {
        onErr('start failed')
      } else {
        onOk()
      }
    })
    stopContinuousRecognitionAsync = vi.fn((onOk: () => void) => onOk())

    constructor(public config: { speechRecognitionLanguage: string }, public audio: unknown) {
      sdkState.recognizer = this as never
    }

    get language() {
      return this.config.speechRecognitionLanguage
    }
  }
  return {
    SpeechConfig,
    AudioConfig,
    SpeechRecognizer,
    ResultReason: { RecognizedSpeech: 3, NoMatch: 0 },
    CancellationReason: { Error: 1, EndOfStream: 0 },
  }
})

function tokenResponse(overrides: Record<string, unknown> = {}) {
  return new Response(
    JSON.stringify({
      token: 'service-token',
      region: 'eastus2',
      language: 'ja-JP',
      expires_at: Math.floor(Date.now() / 1000) + 540,
      ...overrides,
    }),
  )
}

const originalFetch = globalThis.fetch

describe('AzureSpeechRecognizer', () => {
  beforeEach(() => {
    sdkState.recognizer = null
    sdkState.fromAuthCalls = []
    sdkState.startShouldFail = false
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('fetches a keyless token and wires recognition events to onTranscript', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(tokenResponse())
    const onTranscript = vi.fn()
    const onStateChange = vi.fn()
    const recognizer = new AzureSpeechRecognizer('/api/speech-token', {
      onTranscript,
      onError: vi.fn(),
      onStateChange,
    })

    await recognizer.start()

    expect(sdkState.fromAuthCalls).toEqual([['service-token', 'eastus2']])
    expect(sdkState.recognizer?.language).toBe('ja-JP')
    expect(onStateChange).toHaveBeenCalledWith('connecting')
    expect(onStateChange).toHaveBeenCalledWith('listening')

    sdkState.recognizer?.recognizing?.(null, { result: { text: '沖縄' } })
    expect(onTranscript).toHaveBeenCalledWith('沖縄', false)

    sdkState.recognizer?.recognized?.(null, { result: { reason: 3, text: '沖縄ファミリー旅行' } })
    expect(onTranscript).toHaveBeenCalledWith('沖縄ファミリー旅行', true)
  })

  it('ignores recognized events that are not recognized speech', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(tokenResponse())
    const onTranscript = vi.fn()
    const recognizer = new AzureSpeechRecognizer('/api/speech-token', {
      onTranscript,
      onError: vi.fn(),
      onStateChange: vi.fn(),
    })

    await recognizer.start()
    sdkState.recognizer?.recognized?.(null, { result: { reason: 0, text: 'noise' } })

    expect(onTranscript).not.toHaveBeenCalled()
  })

  it('reports an error and stops on a canceled error event', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(tokenResponse())
    const onError = vi.fn()
    const onStateChange = vi.fn()
    const recognizer = new AzureSpeechRecognizer('/api/speech-token', {
      onTranscript: vi.fn(),
      onError,
      onStateChange,
    })

    await recognizer.start()
    const sdkRecognizer = sdkState.recognizer
    sdkRecognizer?.canceled?.(null, { reason: 1, errorDetails: 'mic blocked' })

    expect(onError).toHaveBeenCalledWith('mic blocked')
    expect(onStateChange).toHaveBeenCalledWith('stopped')
    expect(sdkRecognizer?.close).toHaveBeenCalled()
  })

  it('rejects when the token endpoint is unavailable', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response('unavailable', { status: 503 }))
    const recognizer = new AzureSpeechRecognizer('/api/speech-token', {
      onTranscript: vi.fn(),
      onError: vi.fn(),
      onStateChange: vi.fn(),
    })

    await expect(recognizer.start()).rejects.toThrow(/HTTP 503/)
  })

  it('rejects when the token response is missing fields', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ region: 'eastus2' })))
    const recognizer = new AzureSpeechRecognizer('/api/speech-token', {
      onTranscript: vi.fn(),
      onError: vi.fn(),
      onStateChange: vi.fn(),
    })

    await expect(recognizer.start()).rejects.toThrow(/missing token/)
  })

  it('stops recognition and releases the recognizer', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(tokenResponse())
    const onStateChange = vi.fn()
    const recognizer = new AzureSpeechRecognizer('/api/speech-token', {
      onTranscript: vi.fn(),
      onError: vi.fn(),
      onStateChange,
    })

    await recognizer.start()
    const sdkRecognizer = sdkState.recognizer
    recognizer.stop()

    expect(sdkRecognizer?.stopContinuousRecognitionAsync).toHaveBeenCalled()
    expect(sdkRecognizer?.close).toHaveBeenCalled()
    expect(onStateChange).toHaveBeenLastCalledWith('stopped')
  })
})
