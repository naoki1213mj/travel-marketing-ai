import type { TextContent } from '../hooks/useSSE'
import { MarkdownView } from './MarkdownView'

interface AnalysisViewProps {
  contents: TextContent[]
  t: (key: string) => string
}

export function AnalysisView({ contents, t }: AnalysisViewProps) {
  const analysisContent = contents.findLast(c => c.agent === 'data-search-agent')
  if (!analysisContent) return null

  return (
    <div className="rounded-[24px] border border-[var(--panel-border)] bg-[var(--panel-strong)] p-5">
      <h3 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">
        {t('section.analysis')}
      </h3>
      <MarkdownView content={analysisContent.content} />
    </div>
  )
}
