import type { TextContent } from '../hooks/useSSE'

interface BrochurePreviewProps {
  contents: TextContent[]
}

export function BrochurePreview({ contents }: BrochurePreviewProps) {
  const htmlContent = contents.find(
    c => c.agent === 'brochure-gen-agent' && c.content_type === 'html'
  )
  if (!htmlContent) return null

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">
        🎨 ブローシャプレビュー
      </h3>
      <div className="overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700">
        <iframe
          srcDoc={htmlContent.content}
          title="ブローシャプレビュー"
          className="h-96 w-full bg-white"
          sandbox="allow-same-origin"
        />
      </div>
    </div>
  )
}
