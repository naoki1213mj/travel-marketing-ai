import type { TextContent } from '../hooks/useSSE'

interface RegulationResultsProps {
  contents: TextContent[]
}

export function RegulationResults({ contents }: RegulationResultsProps) {
  const regulationContent = contents.find(c => c.agent === 'regulation-check-agent')
  if (!regulationContent) return null

  return (
    <div className="rounded-lg bg-green-50 p-4 dark:bg-green-950">
      <h3 className="mb-2 text-sm font-medium text-green-800 dark:text-green-300">
        ⚖️ レギュレーションチェック
      </h3>
      <div className="prose prose-sm max-w-none text-gray-700 dark:prose-invert dark:text-gray-300">
        {regulationContent.content.split('\n').map((line, i) => {
          if (line.startsWith('## ')) return <h4 key={i} className="mt-3 text-sm font-semibold">{line.slice(3)}</h4>
          if (line.includes('✅')) return <p key={i} className="text-green-700 dark:text-green-400">{line}</p>
          if (line.includes('⚠️')) return <p key={i} className="text-amber-700 dark:text-amber-400">{line}</p>
          if (line.includes('❌')) return <p key={i} className="text-red-700 dark:text-red-400">{line}</p>
          if (line.trim()) return <p key={i}>{line}</p>
          return null
        })}
      </div>
    </div>
  )
}
