import { useState } from 'react'
import type { ApprovalRequest } from '../hooks/useSSE'

interface PlanApprovalProps {
  request: ApprovalRequest
  onApprove: (response: string) => void
  t: (key: string) => string
}

export function PlanApproval({ request, onApprove, t }: PlanApprovalProps) {
  const [revision, setRevision] = useState('')
  const [mode, setMode] = useState<'view' | 'revise'>('view')

  return (
    <div className="space-y-4 rounded-lg bg-amber-50 p-4 dark:bg-amber-950">
      <h3 className="text-sm font-medium text-amber-800 dark:text-amber-300">
        ✅ {t('approval.title')}
      </h3>

      {request.plan_markdown && (
        <div className="prose prose-sm max-w-none rounded-lg bg-white p-4 text-gray-700
                        dark:bg-gray-800 dark:prose-invert dark:text-gray-300">
          {request.plan_markdown.split('\n').map((line, i) => {
            if (line.startsWith('# ')) return <h2 key={i} className="text-lg font-bold">{line.slice(2)}</h2>
            if (line.startsWith('## ')) return <h3 key={i} className="text-base font-semibold">{line.slice(3)}</h3>
            if (line.startsWith('- ')) return <li key={i}>{line.slice(2)}</li>
            if (line.trim()) return <p key={i}>{line}</p>
            return <br key={i} />
          })}
        </div>
      )}

      <p className="text-sm text-gray-600 dark:text-gray-400">{request.prompt}</p>

      {mode === 'view' ? (
        <div className="flex gap-3">
          <button
            onClick={() => setMode('revise')}
            className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium
                       text-gray-700 hover:bg-gray-50
                       dark:border-gray-600 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700"
            autoFocus
          >
            ✏️ {t('approval.revise')}
          </button>
          <button
            onClick={() => onApprove('承認')}
            className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white
                       hover:bg-green-700 dark:bg-green-500 dark:hover:bg-green-600"
          >
            ✅ {t('approval.approve')}
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <textarea
            value={revision}
            onChange={e => setRevision(e.target.value)}
            placeholder={t('approval.prompt')}
            rows={3}
            className="w-full resize-none rounded-lg border border-gray-200 bg-white px-3 py-2
                       text-sm focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-100
                       dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100"
            autoFocus
          />
          <div className="flex gap-2">
            <button
              onClick={() => setMode('view')}
              className="rounded-lg border border-gray-300 px-3 py-1.5 text-sm text-gray-600
                         dark:border-gray-600 dark:text-gray-400"
            >
              戻る
            </button>
            <button
              onClick={() => { if (revision.trim()) onApprove(revision.trim()) }}
              disabled={!revision.trim()}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white
                         hover:bg-blue-700 disabled:opacity-40
                         dark:bg-blue-500 dark:hover:bg-blue-600"
            >
              {t('input.send')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
