import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VoiceInput } from './VoiceInput'

const originalFetch = globalThis.fetch
const originalSpeechRecognition = (window as Window & typeof globalThis & { SpeechRecognition?: unknown }).SpeechRecognition
const originalWebkitSpeechRecognition = (window as Window & typeof globalThis & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition

vi.mock('../lib/msal-auth', () => ({
  getVoiceLiveToken: vi.fn(),
}))

vi.mock('../lib/voice-live', () => ({
  VoiceLiveClient: class {
    async connect() {}
    disconnect() {}
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
  'voice.provider': 'Voice Live',
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
    sessionStorage.removeItem('voiceLiveFailed')
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

  it('skips Voice Live config lookup when capability is unavailable', async () => {
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

  it('disables Talk to start when the capability is unavailable', async () => {
    render(<VoiceInput onTranscript={vi.fn()} voiceTalkToStartAvailable={false} t={t} />)

    expect(screen.getByRole('button', { name: '話して開始' })).toBeDisabled()
    expect(screen.getByText('音声入力は現在利用できません')).toBeInTheDocument()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })
})
