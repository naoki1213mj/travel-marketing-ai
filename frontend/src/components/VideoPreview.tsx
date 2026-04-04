import { Download } from 'lucide-react'

interface VideoPreviewProps {
  videoUrl?: string
  statusMessage?: string
  backgroundPending?: boolean
  t: (key: string) => string
}

export function VideoPreview({ videoUrl, statusMessage, backgroundPending = false, t }: VideoPreviewProps) {
  if (!videoUrl) {
    return (
      <div className="flex items-center justify-center rounded-[24px] border border-dashed border-[var(--panel-border)] bg-[var(--panel-strong)] p-12">
        <div className="text-center">
          <svg className="mx-auto h-10 w-10 text-[var(--text-muted)]" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.2}><path strokeLinecap="round" strokeLinejoin="round" d="m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z" /></svg>
          <p className="mt-3 text-sm font-medium text-[var(--text-primary)]">{t('tab.video')}</p>
          <p className="mt-2 text-sm text-[var(--text-secondary)]">
            {backgroundPending
              ? t('video.pending.description')
              : statusMessage || t('tab.video.description')}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-[24px] border border-[var(--panel-border)] bg-[var(--panel-strong)]">
      <video
        src={videoUrl}
        controls
        className="w-full"
        preload="metadata"
      >
        <track kind="captions" />
        {t('video.unsupported')}
      </video>
      <div className="p-3">
        <a
          href={videoUrl}
          download="avatar-video.mp4"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-3 inline-flex items-center gap-1 rounded-full border border-[var(--panel-border)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--accent-soft)] transition-colors"
        >
          <Download className="h-3.5 w-3.5" /> {t('export.video')}
        </a>
      </div>
    </div>
  )
}
