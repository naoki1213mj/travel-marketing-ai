import { Download } from 'lucide-react'
import type { ImageContent } from '../hooks/useSSE'

interface ImageGalleryProps {
  images: ImageContent[]
  t: (key: string) => string
}

export function ImageGallery({ images, t }: ImageGalleryProps) {
  const validImages = images.filter(image => image.url.trim().length > 0)

  if (validImages.length === 0) {
    return (
      <div className="rounded-[24px] border border-dashed border-[var(--panel-border)] bg-[var(--panel-strong)] px-6 py-10 text-sm text-[var(--text-muted)]">
        {t('preview.unavailable')}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-[var(--text-primary)]">
        {t('section.images')}
      </h3>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {validImages.map((img, i) => (
          <div key={i} className="overflow-hidden rounded-[24px] border border-[var(--panel-border)] bg-[var(--panel-strong)] p-3">
            <img
              src={img.url}
              alt={img.alt}
              className="h-auto w-full rounded-[18px] object-cover"
            />
            <p className="mt-2 text-xs text-[var(--text-muted)]">{img.alt}</p>
            <a
              href={img.url}
              download={`travel-image-${i + 1}.png`}
              className="mt-2 inline-flex items-center gap-1 rounded-full border border-[var(--panel-border)] px-3 py-1 text-xs text-[var(--text-secondary)] hover:bg-[var(--accent-soft)] transition-colors"
            >
              <Download className="h-3.5 w-3.5" /> {t('export.image')}
            </a>
          </div>
        ))}
      </div>
    </div>
  )
}
