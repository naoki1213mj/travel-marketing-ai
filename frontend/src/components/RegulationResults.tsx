import type { TextContent } from '../hooks/useSSE'
import { MarkdownView } from './MarkdownView'

interface RegulationResultsProps {
  contents: TextContent[]
  t: (key: string) => string
}

export function RegulationResults({ contents, t }: RegulationResultsProps) {
  const regulationContent = contents.find(c => c.agent === 'regulation-check-agent')
  if (!regulationContent) return null

  return (
    <div className="rounded-[24px] border border-[var(--success-border)] bg-[var(--success-surface)] p-5">
      <h3 className="mb-3 text-sm font-medium text-[var(--success-text)]">
        {t('section.regulation')}
      </h3>
      <MarkdownView content={regulationContent.content} />
    </div>
  )
}
