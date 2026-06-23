import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VoiceInput } from './VoiceInput'

const originalFetch = globalThis.fetch
const originalSpeechRecognition = (window as Window & typeof globalThis & { SpeechRecognition?: unknown }).SpeechRecognition
const originalWebkitSpeechRecognition = (window as Window & typeof globalThis & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition

const speechMocks = vi.hoisted(() => ({
  start: vi.fn(),
  stop: vi.fn(),
  handlers: { current: null as null | {
    onTranscript: (text: string, isFinal: boolean) => void
    onError: (error: string) => void
    onStateChange: (state: 'connecting' | 'listening' | 'stopped') => void
  } },
}))

vi.mock('../lib/speech-stt', () => ({
  AzureSpeechRecognizer: class {
    constructor(_tokenUrl: string, handlers: {
      onTranscript: (text: string, isFinal: boolean) => void
      onError: (error: string) => void
      onStateChange: (state: 'connecting' | 'listening' | 'stopped') => void
    }) {
      speechMocks.handlers.current = handlers
    }

    async start() {
      speechMocks.start()
      speechMocks.handlers.current?.onStateChange('listening')
    }

    stop() {
      speechMocks.stop()
    }
  },
}))

class MockSpeechRecognition extends EventTarget {
  static instances: MockSpeechRecognition[] = []

  continuous = false
  interimResults = false
  lang = ''
  onresult: ((event: {
    resultIndex: number
    results: {
      length: number
      item: (index: number) => { isFinal: boolean; length: number; item: (altIndex: number) => { transcript: string; confidence: number }; [index: number]: { transcript: string; confidence: number } }
      [index: number]: { isFinal: boolean; length: number; item: (altIndex: number) => { transcript: string; confidence: number }; [index: number]: { transcript: string; confidence: number } }
    }
  }) => void) | null = null
  onerror: ((event: { error: string }) => void) | null = null
  onend: (() => void) | null = null
  start = vi.fn()
  stop = vi.fn()
  abort = vi.fn()

  constructor() {
    super()
    MockSpeechRecognition.instances.push(this)
  }
}

const t = (key: string) => ({
  'voice.label': '音声入力',
  'voice.talk_to_start': '話して開始',
  'voice.review_hint': '文字起こしを確認・編集してから送信してください',
  'voice.unavailable': '音声入力は現在利用できません',
  'voice.listening': '音声を認識中…',
  'voice.processing': '処理中…',
  'voice.speaking': '読み上げ中…',
  'voice.connecting': '接続中…',
  'voice.unsupported': 'このブラウザは音声入力に対応していません',
  'voice.provider': 'Azure Speech',
}[key] ?? key)

function createSpeechResult(transcript: string, isFinal: boolean) {
  const alternative = { transcript, confidence: 0.9 }
  return {
    isFinal,
    length: 1,
    item: () => alternative,
    0: alternative,
  }
}

function createSpeechEvent(
  results: Array<{ transcript: string; isFinal: boolean }>,
  resultIndex = 0,
) {
  const speechResults = results.map(result => createSpeechResult(result.transcript, result.isFinal))
  return {
    resultIndex,
    results: {
      length: speechResults.length,
      item: (index: number) => speechResults[index],
      ...speechResults,
    },
  }
}

describe('VoiceInput', () => {
  beforeEach(() => {
    MockSpeechRecognition.instances = []
    speechMocks.start.mockClear()
    speechMocks.stop.mockClear()
    speechMocks.handlers.current = null
    sessionStorage.removeItem('azureSpeechFailed')
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({})))
    Object.defineProperty(window, 'SpeechRecognition', {
      configurable: true,
      writable: true,
      value: MockSpeechRecognition,
    })
    Object.defineProperty(window, 'webkitSpeechRecognition', {
      configurable: true,
      writable: true,
      value: undefined,
    })
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    if (originalSpeechRecognition === undefined) {
      delete (window as Window & typeof globalThis & { SpeechRecognition?: unknown }).SpeechRecognition
    } else {
      Object.defineProperty(window, 'SpeechRecognition', {
        configurable: true,
        writable: true,
        value: originalSpeechRecognition,
      })
    }

    if (originalWebkitSpeechRecognition === undefined) {
      delete (window as Window & typeof globalThis & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
    } else {
      Object.defineProperty(window, 'webkitSpeechRecognition', {
        configurable: true,
        writable: true,
        value: originalWebkitSpeechRecognition,
      })
    }
  })

  it('ignores stale Web Speech callbacks after a new recording session starts', async () => {
    render(<VoiceInput onTranscript={vi.fn()} t={t} />)

    const button = screen.getByRole('button', { name: '話して開始' })
    await waitFor(() => {
      expect(button).not.toBeDisabled()
    })

    fireEvent.click(button)
    expect(MockSpeechRecognition.instances).toHaveLength(1)
    expect(screen.getByText('音声を認識中…')).toBeInTheDocument()

    fireEvent.click(button)
    fireEvent.click(button)

    expect(MockSpeechRecognition.instances).toHaveLength(2)
    expect(screen.getByText('音声を認識中…')).toBeInTheDocument()

    act(() => {
      MockSpeechRecognition.instances[0].onend?.()
    })

    expect(screen.getByText('音声を認識中…')).toBeInTheDocument()
    expect(MockSpeechRecognition.instances[0].stop).toHaveBeenCalledTimes(1)
  })

  it('skips Azure Speech config lookup when capability is unavailable', async () => {
    render(<VoiceInput onTranscript={vi.fn()} voiceLiveAvailable={false} t={t} />)

    const button = screen.getByRole('button', { name: '話して開始' })
    await waitFor(() => {
      expect(button).not.toBeDisabled()
    })

    expect(globalThis.fetch).not.toHaveBeenCalled()
    fireEvent.click(button)
    expect(MockSpeechRecognition.instances).toHaveLength(1)
  })

  it('accumulates interim and final Web Speech transcripts for review', async () => {
    const onTranscript = vi.fn()
    render(<VoiceInput onTranscript={onTranscript} voiceLiveAvailable={false} t={t} />)

    const button = screen.getByRole('button', { name: '話して開始' })
    await waitFor(() => {
      expect(button).not.toBeDisabled()
    })

    fireEvent.click(button)
    const recognition = MockSpeechRecognition.instances[0]

    act(() => {
      recognition.onresult?.(createSpeechEvent([{ transcript: '春の沖縄', isFinal: false }]))
    })

    expect(onTranscript).toHaveBeenLastCalledWith('春の沖縄')
    expect(screen.getByText('文字起こしを確認・編集してから送信してください')).toBeInTheDocument()
    expect(screen.getByText('春の沖縄')).toBeInTheDocument()

    act(() => {
      recognition.onresult?.(createSpeechEvent([{ transcript: '春の沖縄', isFinal: true }]))
    })

    expect(onTranscript).toHaveBeenLastCalledWith('春の沖縄')

    act(() => {
      recognition.onresult?.(createSpeechEvent([
        { transcript: '春の沖縄', isFinal: true },
        { transcript: 'ファミリー向け', isFinal: true },
      ], 1))
    })

    expect(onTranscript).toHaveBeenLastCalledWith('春の沖縄 ファミリー向け')
    expect(screen.getByText('春の沖縄 ファミリー向け')).toBeInTheDocument()
  })

  it('uses Azure Speech recognition and publishes the transcript when configured', async () => {
    const onTranscript = vi.fn()
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ available: true, mode: 'azure_speech' })))
    render(<VoiceInput onTranscript={onTranscript} voiceLiveAvailable={true} voiceTalkToStartAvailable={true} t={t} />)

    const button = screen.getByRole('button', { name: '話して開始' })
    await waitFor(() => {
      expect(button).not.toBeDisabled()
    })

    fireEvent.click(button)

    await waitFor(() => {
      expect(speechMocks.start).toHaveBeenCalledTimes(1)
    })
    // Web Speech にはフォールバックせず Azure Speech を使う
    expect(MockSpeechRecognition.instances).toHaveLength(0)
    expect(screen.getByText('音声を認識中…')).toBeInTheDocument()

    act(() => {
      speechMocks.handlers.current?.onTranscript('沖縄 ファミリー旅行', true)
    })

    expect(onTranscript).toHaveBeenLastCalledWith('沖縄 ファミリー旅行')
    expect(screen.getByText('沖縄 ファミリー旅行')).toBeInTheDocument()
  })

  it('falls back to Web Speech when Azure Speech fails to start', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ available: true, mode: 'azure_speech' })))
    speechMocks.start.mockImplementationOnce(() => {
      throw new Error('token unavailable')
    })
    render(<VoiceInput onTranscript={vi.fn()} voiceLiveAvailable={true} voiceTalkToStartAvailable={true} t={t} />)

    const button = screen.getByRole('button', { name: '話して開始' })
    await waitFor(() => {
      expect(button).not.toBeDisabled()
    })

    fireEvent.click(button)

    await waitFor(() => {
      expect(MockSpeechRecognition.instances).toHaveLength(1)
    })
    expect(sessionStorage.getItem('azureSpeechFailed')).toBe('true')
  })

  it('disables Talk to start when the capability is unavailable', async () => {
    render(<VoiceInput onTranscript={vi.fn()} voiceTalkToStartAvailable={false} t={t} />)

    expect(screen.getByRole('button', { name: '話して開始' })).toBeDisabled()
    expect(screen.getByText('音声入力は現在利用できません')).toBeInTheDocument()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })
})
