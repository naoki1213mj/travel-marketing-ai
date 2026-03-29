import { useCallback, useRef, useState } from 'react'

interface VoiceInputProps {
  onTranscript: (text: string) => void
  disabled?: boolean
}

export function VoiceInput({ onTranscript, disabled = false }: VoiceInputProps) {
  const [isRecording, setIsRecording] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const startRecording = useCallback(async () => {
    try {
      setError(null)
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mediaRecorder = new MediaRecorder(stream)
      mediaRecorderRef.current = mediaRecorder
      chunksRef.current = []

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      mediaRecorder.onstop = () => {
        stream.getTracks().forEach(track => track.stop())
        // Voice Live API との統合はプレビュー
        // 現時点では録音完了のコールバックのみ
        onTranscript('（音声入力: Voice Live API 統合予定）')
      }

      mediaRecorder.start()
      setIsRecording(true)
    } catch (err) {
      setError('マイクへのアクセスが拒否されました')
    }
  }, [onTranscript])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current?.state === 'recording') {
      mediaRecorderRef.current.stop()
    }
    setIsRecording(false)
  }, [])

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={isRecording ? stopRecording : startRecording}
        disabled={disabled}
        className={`rounded-full p-2.5 transition-all duration-200 ${
          isRecording
            ? 'animate-pulse bg-red-500 text-white shadow-lg shadow-red-200 dark:shadow-red-900'
            : 'bg-gray-100 text-gray-600 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'
        } ${disabled ? 'cursor-not-allowed opacity-50' : ''}`}
        aria-label={isRecording ? '録音停止' : '音声入力'}
        title={isRecording ? '録音停止' : '音声入力 (Voice Live)'}
      >
        {isRecording ? (
          <svg className="h-5 w-5" fill="currentColor" viewBox="0 0 20 20">
            <rect x="6" y="6" width="8" height="8" rx="1" />
          </svg>
        ) : (
          <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
          </svg>
        )}
      </button>
      {error && <span className="text-xs text-red-500">{error}</span>}
      {isRecording && <span className="text-xs text-red-500 animate-pulse">● 録音中...</span>}
    </div>
  )
}
