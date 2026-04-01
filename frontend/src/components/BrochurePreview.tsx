import type { TextContent } from '../hooks/useSSE'

interface BrochurePreviewProps {
  contents: TextContent[]
  t: (key: string) => string
}

export function BrochurePreview({ contents, t }: BrochurePreviewProps) {
  const htmlContent = contents.findLast(
    c => c.agent === 'brochure-gen-agent' && c.content_type === 'html'
  )
  if (!htmlContent) {
    return (
      <div className="rounded-[24px] border border-dashed border-[var(--panel-border)] bg-[var(--panel-strong)] px-6 py-10 text-sm text-[var(--text-muted)]">
        {t('preview.unavailable')}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-[var(--text-primary)]">
        {t('section.brochure')}
      </h3>
      <div className="h-[630px] overflow-auto rounded-[24px] border border-[var(--panel-border)] bg-[var(--panel-bg)]">
        <div className="origin-top-left scale-[0.45] transform">
          <iframe
            srcDoc={htmlContent.content}
            title={t('section.brochure')}
            className="h-[1400px] w-[222.25%] border-0 bg-white"
            sandbox=""
          />
        </div>
      </div>
    </div>
  )
}
