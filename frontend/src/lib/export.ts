/**
 * エクスポートユーティリティ。成果物をダウンロードする。
 */

import type { ImageContent, TextContent } from '../hooks/useSSE'

function sanitizeHtmlForExport(html: string): string {
  const documentFragment = new DOMParser().parseFromString(html, 'text/html')
  const blockedTags = ['script', 'iframe', 'form', 'object', 'embed', 'base', 'meta']

  blockedTags.forEach(tagName => {
    documentFragment.querySelectorAll(tagName).forEach(node => node.remove())
  })

  documentFragment.querySelectorAll('*').forEach(node => {
    Array.from(node.attributes).forEach(attribute => {
      const attributeName = attribute.name.toLowerCase()
      const attributeValue = attribute.value.trim().toLowerCase()
      if (attributeName.startsWith('on')) {
        node.removeAttribute(attribute.name)
      }
      if (
        (attributeName === 'href' || attributeName === 'src') &&
        (attributeValue.startsWith('javascript:') || attributeValue.startsWith('data:text/html'))
      ) {
        node.removeAttribute(attribute.name)
      }
    })
  })

  return documentFragment.documentElement.outerHTML
}

function downloadBlob(content: string, filename: string, mimeType: string) {
  const blob = new Blob([content], { type: mimeType })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function downloadDataUrl(dataUrl: string, filename: string) {
  const a = document.createElement('a')
  a.href = dataUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

/** 企画書を Markdown ファイルとしてダウンロード */
export function exportPlanMarkdown(contents: TextContent[]) {
  const revised = contents.find(c => c.agent === 'plan-revision-agent')
  const plan = revised || contents.find(c => c.agent === 'marketing-plan-agent')
  if (!plan) return
  downloadBlob(plan.content, 'marketing-plan.md', 'text/markdown;charset=utf-8')
}

/** ブローシャを HTML ファイルとしてダウンロード */
export function exportBrochureHtml(contents: TextContent[]) {
  const brochure = contents.find(c => c.agent === 'brochure-gen-agent' && c.content_type === 'html')
  if (!brochure) return
  downloadBlob(sanitizeHtmlForExport(brochure.content), 'brochure.html', 'text/html;charset=utf-8')
}

/** 画像を PNG としてダウンロード */
export function exportImage(image: ImageContent, index: number) {
  downloadDataUrl(image.url, `image-${index + 1}.png`)
}

/** 全成果物を JSON で一括エクスポート */
export function exportAllAsJson(
  contents: TextContent[],
  images: ImageContent[],
  conversationId: string | null,
) {
  const data = {
    metadata: {
      conversation_id: conversationId,
      exported_at: new Date().toISOString(),
    },
    plan: contents.find(c => c.agent === 'marketing-plan-agent')?.content || null,
    revised_plan: contents.find(c => c.agent === 'plan-revision-agent')?.content || null,
    regulation_check: contents.find(c => c.agent === 'regulation-check-agent')?.content || null,
    brochure_html: contents.find(c => c.agent === 'brochure-gen-agent' && c.content_type === 'html')?.content || null,
    analysis: contents.find(c => c.agent === 'data-search-agent')?.content || null,
    images: images.map(img => ({ url: img.url, alt: img.alt })),
  }
  downloadBlob(JSON.stringify(data, null, 2), 'artifacts.json', 'application/json;charset=utf-8')
}
