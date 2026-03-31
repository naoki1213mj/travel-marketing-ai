/**
 * Markdown レンダラーコンポーネント。react-markdown で安全に描画する。
 */

import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface MarkdownViewProps {
  content: string
  className?: string
}

export const MarkdownView = memo(function MarkdownView({ content, className = '' }: MarkdownViewProps) {
  return (
    <div
      className={[
        'prose prose-sm max-w-none dark:prose-invert',
        'text-[var(--text-secondary)]',
        'prose-headings:text-[var(--text-primary)] prose-headings:font-semibold',
        'prose-p:text-[var(--text-secondary)] prose-p:leading-relaxed',
        'prose-strong:text-[var(--text-primary)] prose-li:text-[var(--text-secondary)]',
        'prose-code:text-[var(--accent-strong)] prose-a:text-[var(--accent-strong)]',
        'prose-blockquote:border-[var(--accent)] prose-blockquote:text-[var(--text-secondary)]',
        'prose-hr:border-[var(--panel-border)]',
        'prose-pre:border prose-pre:border-[var(--panel-border)] prose-pre:bg-[var(--panel-strong)] prose-pre:rounded-lg',
        'prose-th:text-[var(--text-primary)] prose-th:font-medium prose-th:bg-[var(--panel-strong)] prose-th:px-3 prose-th:py-1.5',
        'prose-td:text-[var(--text-secondary)] prose-td:px-3 prose-td:py-1.5',
        'prose-table:border-collapse prose-table:w-full',
        '[&_table]:border [&_table]:border-[var(--panel-border)] [&_table]:rounded-lg',
        '[&_th]:border [&_th]:border-[var(--panel-border)]',
        '[&_td]:border [&_td]:border-[var(--panel-border)]',
        '[&_tr:nth-child(even)]:bg-[var(--panel-strong)]',
        className,
      ].join(' ')}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  )
})
