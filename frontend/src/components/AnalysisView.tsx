import type { ImageContent, TextContent } from '../hooks/useSSE'
import { ImageGallery } from './ImageGallery'
import { MarkdownView } from './MarkdownView'

interface AnalysisViewProps {
  contents: TextContent[]
  images?: ImageContent[]
  t: (key: string) => string
}

const MARKDOWN_IMAGE_RE = /!\[[^\]]*]\((?:[^()\\]|\\.)*\)/g
const HTML_IMAGE_RE = /<img\b[^>]*>/gi

function stripEmbeddedImageMarkup(content: string): string {
  return content
    .replace(MARKDOWN_IMAGE_RE, '')
    .replace(HTML_IMAGE_RE, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

export function AnalysisView({ contents, images = [], t }: AnalysisViewProps) {
  const analysisContent = contents.findLast(c => c.agent === 'data-search-agent')
  if (!analysisContent) return null

  const analysisImages = images.filter(image => image.agent === 'data-search-agent')
  const sanitizedContent = stripEmbeddedImageMarkup(analysisContent.content)

  return (
    <div className="rounded-[24px] border border-[var(--panel-border)] bg-[var(--panel-strong)] p-5">
      <h3 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">
        {t('section.analysis')}
      </h3>
      {sanitizedContent ? <MarkdownView content={sanitizedContent} /> : null}
      {analysisImages.length > 0 ? (
        <div className={sanitizedContent ? 'mt-5' : ''}>
          <ImageGallery images={analysisImages} t={t} />
        </div>
      ) : null}
    </div>
  )
}
