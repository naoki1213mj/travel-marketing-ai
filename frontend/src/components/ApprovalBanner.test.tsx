import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ApprovalBanner } from './ApprovalBanner'

const t = (key: string) => ({
  'approval.title': '企画書の確認',
  'approval.approve': '承認',
  'approval.revise': '修正',
  'approval.prompt': '修正内容を入力してください',
  'approval.back': '戻る',
  'approval.message': '企画書を確認し、承認するか修正内容を入力してください。',
  'approval.manager.comment': '上司からの差し戻しコメント',
  'approval.diff.title': '変更差分',
  'approval.diff.summary': '追加 {added} 行 / 削除 {removed} 行 / 変更なし {unchanged} 行',
  'approval.diff.added': '追加',
  'approval.diff.removed': '削除',
  'approval.diff.previous': '比較元',
  'approval.diff.current': '確認対象',
  'approval.diff.no_changes': '変更差分はありません。',
  'input.send': '送信',
}[key] ?? key)

describe('ApprovalBanner', () => {
  const request = {
    prompt: '確認してください',
    conversation_id: 'conversation-1',
    plan_markdown: '# Plan\nNew copy',
  }

  it('shows a safe approval diff and preserves approve action', () => {
    const onApprove = vi.fn()
    const { container } = render(
      <ApprovalBanner
        request={request}
        previousPlanMarkdown={'# Plan\nOld copy'}
        onApprove={onApprove}
        t={t}
      />,
    )

    expect(screen.getByText('変更差分')).toBeInTheDocument()
    expect(screen.getByText('Old copy')).toBeInTheDocument()
    expect(screen.getByText('New copy')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-diff-kind="removed"]')).toHaveLength(1)
    expect(container.querySelectorAll('[data-diff-kind="added"]')).toHaveLength(1)
    expect(screen.getByRole('button', { name: /修正/ })).toHaveFocus()

    fireEvent.click(screen.getByRole('button', { name: /承認/ }))

    expect(onApprove).toHaveBeenCalledWith('承認')
  })

  it('keeps the revision submission flow unchanged', () => {
    const onApprove = vi.fn()
    render(
      <ApprovalBanner
        request={request}
        previousPlanMarkdown=""
        onApprove={onApprove}
        t={t}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /修正/ }))
    fireEvent.change(screen.getByPlaceholderText('修正内容を入力してください'), {
      target: { value: 'もっと明るく' },
    })
    fireEvent.click(screen.getByRole('button', { name: '送信' }))

    expect(onApprove).toHaveBeenCalledWith('もっと明るく')
  })
})
