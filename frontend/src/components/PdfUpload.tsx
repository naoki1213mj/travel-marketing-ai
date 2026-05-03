import { useRef, useState } from 'react'
import { apiUrl } from '../lib/api-base'

const MAX_FILE_SIZE = 10 * 1024 * 1024 // 10 MB

interface SourcePayload {
  id: string
  conversation_id: string
  title: string
  summary: string
  status: 'pending_review' | 'reviewed' | 'rejected'
}

interface PdfUploadProps {
  disabled: boolean
  conversationId?: string | null
  onConversationId?: (conversationId: string) => void
  t: (key: string) => string
}

function normalizeSourcePayload(value: unknown): SourcePayload | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const source = raw.source && typeof raw.source === 'object'
    ? raw.source as Record<string, unknown>
    : raw
  const id = typeof source.id === 'string' ? source.id : ''
  const conversationId = typeof source.conversation_id === 'string' ? source.conversation_id : ''
  const title = typeof source.title === 'string' ? source.title : ''
  const summary = typeof source.summary === 'string' ? source.summary : ''
  const status = source.status === 'reviewed' || source.status === 'rejected' ? source.status : 'pending_review'
  if (!id || !conversationId) return null
  return { id, conversation_id: conversationId, title, summary, status }
}

export function PdfUpload({ disabled, conversationId, onConversationId, t }: PdfUploadProps) {
  const [fileName, setFileName] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [reviewing, setReviewing] = useState(false)
  const [result, setResult] = useState<'success' | 'error' | null>(null)
  const [source, setSource] = useState<SourcePayload | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setResult('error')
      return
    }
    if (file.size > MAX_FILE_SIZE) {
      setResult('error')
      return
    }

    setFileName(file.name)
    setUploading(true)
    setResult(null)

    const formData = new FormData()
    formData.append('file', file)
    if (conversationId) {
      formData.append('conversation_id', conversationId)
    }

    try {
      const res = await fetch(apiUrl('/api/sources/pdf'), {
        method: 'POST',
        body: formData,
      })
      if (!res.ok) {
        setResult('error')
        return
      }
      const payload = normalizeSourcePayload(await res.json())
      if (!payload) {
        setResult('error')
        return
      }
      setSource(payload)
      onConversationId?.(payload.conversation_id)
      setResult('success')
    } catch {
      setResult('error')
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const reviewSource = async (approved: boolean) => {
    if (!source) return
    setReviewing(true)
    setResult(null)
    try {
      const res = await fetch(apiUrl(`/api/sources/${encodeURIComponent(source.id)}/review`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approved, summary: source.summary }),
      })
      if (!res.ok) {
        setResult('error')
        return
      }
      const payload = normalizeSourcePayload(await res.json())
      if (!payload) {
        setResult('error')
        return
      }
      setSource(payload)
      setResult('success')
    } catch {
      setResult('error')
    } finally {
      setReviewing(false)
    }
  }

  const deleteSource = async () => {
    if (!source) return
    setReviewing(true)
    setResult(null)
    try {
      const res = await fetch(apiUrl(`/api/sources/${encodeURIComponent(source.id)}`), {
        method: 'DELETE',
      })
      if (!res.ok) {
        setResult('error')
        return
      }
      setSource(null)
      setFileName(null)
    } catch {
      setResult('error')
    } finally {
      setReviewing(false)
    }
  }

  return (
    <div className="flex max-w-xl flex-col gap-2">
      <div className="flex items-center gap-2">
      <label
        className={`flex cursor-pointer items-center gap-1.5 rounded-full border border-[var(--input-border)] bg-[var(--input-bg)] px-3 py-2 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--accent)] hover:text-[var(--accent-strong)] ${disabled || uploading ? 'pointer-events-none opacity-40' : ''}`}
      >
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 0H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" /></svg>
        <span>{t('pdf.upload')}</span>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf"
          className="hidden"
          onChange={handleChange}
          disabled={disabled || uploading}
        />
      </label>
      {uploading && <span className="text-xs text-[var(--text-muted)]">{t('pdf.uploading')}</span>}
      {result === 'success' && fileName && (
        <span className="inline-flex items-center gap-1 text-xs text-green-600"><svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" /></svg> {source?.status === 'reviewed' ? t('pdf.reviewed') : fileName}</span>
      )}
      {result === 'error' && (
        <span className="inline-flex items-center gap-1 text-xs text-red-500"><svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" /></svg> {t('pdf.error')}</span>
      )}
      </div>
      {source && source.status === 'pending_review' && (
        <div className="rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-2 text-xs text-[var(--text-secondary)]">
          <p className="font-medium text-[var(--text-primary)]">{t('pdf.review_required')}</p>
          <p className="mt-1 line-clamp-3 leading-5">{source.summary || source.title}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              disabled={disabled || reviewing}
              onClick={() => { void reviewSource(true) }}
              className="rounded-full bg-[var(--accent)] px-3 py-1 text-[11px] font-semibold text-white disabled:opacity-50"
            >
              {t('pdf.approve')}
            </button>
            <button
              type="button"
              disabled={disabled || reviewing}
              onClick={() => { void deleteSource() }}
              className="rounded-full border border-[var(--panel-border)] px-3 py-1 text-[11px] font-semibold text-[var(--text-secondary)] disabled:opacity-50"
            >
              {t('pdf.delete')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
