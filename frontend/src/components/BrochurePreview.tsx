import type { TextContent } from '../hooks/useSSE'

interface BrochurePreviewProps {
  contents: TextContent[]
  t: (key: string) => string
}

export function BrochurePreview({ contents, t }: BrochurePreviewProps) {
  const htmlContent = contents.find(
    c => c.agent === 'brochure-gen-agent' && c.content_type === 'html'
  )
  if (!htmlContent) return null

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-[var(--text-primary)]">
        {t('section.brochure')}
      </h3>
      <div className="overflow-hidden rounded-[24px] border border-[var(--panel-border)] bg-[var(--panel-bg)]">
        <iframe
          srcDoc={htmlContent.content}
          title={t('section.brochure')}
          className="h-[28rem] w-full bg-white"
          sandbox=""
        />
      </div>
    </div>
  )
}
