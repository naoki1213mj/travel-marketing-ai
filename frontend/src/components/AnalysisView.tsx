import type { TextContent } from '../hooks/useSSE'

interface AnalysisViewProps {
  contents: TextContent[]
}

export function AnalysisView({ contents }: AnalysisViewProps) {
  const analysisContent = contents.find(c => c.agent === 'data-search-agent')
  if (!analysisContent) return null

  return (
    <div className="rounded-lg bg-blue-50 p-4 dark:bg-blue-950">
      <h3 className="mb-2 text-sm font-medium text-blue-800 dark:text-blue-300">
        📊 データ分析結果
      </h3>
      <div className="prose prose-sm max-w-none text-gray-700 dark:prose-invert dark:text-gray-300">
        {analysisContent.content.split('\n').map((line, i) => {
          if (line.startsWith('## ')) return <h4 key={i} className="mt-3 text-sm font-semibold">{line.slice(3)}</h4>
          if (line.startsWith('**') && line.endsWith('**')) return <p key={i} className="font-bold">{line.slice(2, -2)}</p>
          if (line.trim()) return <p key={i}>{line}</p>
          return null
        })}
      </div>
    </div>
  )
}
