/**
 * Markdown レンダラーコンポーネント。react-markdown で安全に描画する。
 */

import { memo } from 'react'
import ReactMarkdown from 'react-markdown'

interface MarkdownViewProps {
  content: string
  className?: string
}

export const MarkdownView = memo(function MarkdownView({ content, className = '' }: MarkdownViewProps) {
  return (
    <div
      className={[
        'prose prose-sm max-w-none text-[var(--text-secondary)]',
        'prose-headings:text-[var(--text-primary)] prose-p:text-[var(--text-secondary)]',
        'prose-strong:text-[var(--text-primary)] prose-li:text-[var(--text-secondary)]',
        'prose-code:text-[var(--accent-strong)] prose-a:text-[var(--accent-strong)]',
        'prose-blockquote:border-[var(--accent)] prose-blockquote:text-[var(--text-secondary)]',
        'prose-hr:border-[var(--panel-border)] prose-pre:border prose-pre:border-[var(--panel-border)]',
        'prose-pre:bg-[var(--panel-strong)] prose-th:text-[var(--text-primary)] prose-td:text-[var(--text-secondary)]',
        className,
      ].join(' ')}
    >
      <ReactMarkdown>{content}</ReactMarkdown>
    </div>
  )
})
