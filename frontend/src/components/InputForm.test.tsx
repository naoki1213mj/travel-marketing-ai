import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { InputForm } from './InputForm'

const t = (key: string) => ({
  'input.quick.label': 'すぐ試せる例',
  'input.shortcuts': 'Enter で送信 / Shift+Enter で改行',
  'input.quick.okinawa.label': '春の沖縄ファミリー',
  'input.quick.okinawa.prompt': '春の沖縄ファミリー向けプランを企画して',
  'input.quick.hokkaido.label': '冬の北海道カップル',
  'input.quick.hokkaido.prompt': '冬の北海道カップル向けプランを企画して',
  'input.quick.kyoto.label': '秋の京都シニア',
  'input.quick.kyoto.prompt': '秋の京都シニア向けプランを企画して',
  'input.quick.hawaii.label': '夏のハワイ学生',
  'input.quick.hawaii.prompt': '夏のハワイ学生旅行向けプランを企画して',
}[key] ?? key)

describe('InputForm', () => {
  it('shows quick starts and sends the selected prompt', () => {
    const onSubmit = vi.fn()
    render(
      <InputForm
        onSubmit={onSubmit}
        disabled={false}
        placeholder="旅行プランを入力"
        sendLabel="送信"
        label="新規指示"
        t={t}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '春の沖縄ファミリー' }))
    fireEvent.click(screen.getByRole('button', { name: '送信' }))

    expect(onSubmit).toHaveBeenCalledWith('春の沖縄ファミリー向けプランを企画して')
  })

  it('keeps Shift+Enter as a newline shortcut instead of submitting', () => {
    const onSubmit = vi.fn()
    render(
      <InputForm
        onSubmit={onSubmit}
        disabled={false}
        placeholder="旅行プランを入力"
        sendLabel="送信"
        label="新規指示"
        t={t}
      />,
    )

    const textarea = screen.getByLabelText('新規指示')
    fireEvent.change(textarea, { target: { value: '京都の秋プラン' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true })

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText('Enter で送信 / Shift+Enter で改行')).toBeInTheDocument()
  })
})
