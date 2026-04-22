import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DEFAULT_CONVERSATION_SETTINGS, DEFAULT_SETTINGS, SettingsPanel } from './SettingsPanel'

const translations: Record<string, string> = {
  'settings.title': 'モデル設定',
  'settings.image.title': '画像生成設定',
  'settings.manager.title': '上司承認設定',
  'settings.model': 'モデル',
  'settings.model.desc': '使用する推論モデル',
  'settings.temperature': 'Temperature',
  'settings.temperature.desc': '値が高いほど創造的な出力',
  'settings.maxTokens': '最大トークン数',
  'settings.topP': 'Top P',
  'settings.iqResults': 'IQ 検索結果数',
  'settings.iqThreshold': 'IQ スコア閾値',
  'settings.image.model': '画像モデル',
  'settings.image.model.desc': '画像生成に使用するモデル',
  'settings.image.quality': '画質',
  'settings.image.quality.desc': '高画質ほど生成に時間がかかります',
  'settings.image.width': '幅 (px)',
  'settings.image.width.desc': 'MAI-Image-2: 最小 768px',
  'settings.image.height': '高さ (px)',
  'settings.image.height.desc': 'MAI-Image-2: 最小 768px',
  'settings.image.mai.constraint': 'constraint',
  'settings.manager.enabled': '上司承認を有効化',
  'settings.manager.enabled.desc': 'desc',
  'settings.manager.email': '上司メールアドレス',
  'settings.manager.email.desc': 'メール説明',
  'settings.manager.email.placeholder': 'manager@example.com',
  'settings.manager.email.invalid': 'invalid',
  'settings.workiq.title': 'Work IQ',
  'settings.workiq.enabled': 'Work IQ を有効化',
  'settings.workiq.enabled.desc': 'Work IQ 説明',
  'settings.workiq.status': '状態',
  'settings.workiq.status.off': 'オフ',
  'settings.workiq.status.ready': '次の会話で確認',
  'settings.workiq.status.enabled': 'この会話で有効',
  'settings.workiq.status.sign_in_required': 'サインインが必要',
  'settings.workiq.status.consent_required': '管理者の同意が必要',
  'settings.workiq.status.unavailable': '現在利用できません',
  'settings.workiq.message.off': 'Work IQ はオフです',
  'settings.workiq.message.ready': '次の会話で確認します',
  'settings.workiq.message.enabled': 'この会話で使用します',
  'settings.workiq.message.sign_in_required': 'サインインしてください',
  'settings.workiq.message.consent_required': '同意が必要です',
  'settings.workiq.message.unavailable': '利用できません',
  'settings.workiq.locked': '新しい会話を開始してください',
  'settings.workiq.sources': '参照ソース',
  'settings.workiq.source.meeting_notes': '会議メモ',
  'settings.workiq.source.emails': 'メール',
  'settings.workiq.source.teams_chats': 'Teams チャット',
  'settings.workiq.source.documents_notes': 'ドキュメント/ノート',
  'settings.reset': 'デフォルトに戻す',
}

function t(key: string): string {
  return translations[key] ?? key
}

describe('SettingsPanel', () => {
  it('renders separated buttons for model, image, and manager settings', () => {
    render(
      <SettingsPanel
        settings={DEFAULT_SETTINGS}
        conversationSettings={DEFAULT_CONVERSATION_SETTINGS}
        workIqStatus="off"
        onChange={() => {}}
        onConversationSettingsChange={() => {}}
        t={t}
      />,
    )

    expect(screen.getByRole('button', { name: /モデル設定/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /画像生成設定/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /上司承認設定/ })).toBeTruthy()
    expect(screen.getByRole('button', { name: /Work IQ/ })).toBeTruthy()
  })

  it('shows only the selected manager settings section', () => {
    render(
      <SettingsPanel
        settings={DEFAULT_SETTINGS}
        conversationSettings={DEFAULT_CONVERSATION_SETTINGS}
        workIqStatus="off"
        onChange={() => {}}
        onConversationSettingsChange={() => {}}
        t={t}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /上司承認設定/ }))

    expect(screen.getByText('上司承認を有効化')).toBeTruthy()
    expect(screen.queryByLabelText('モデル')).toBeNull()
    expect(screen.queryByLabelText('画像モデル')).toBeNull()
  })

  it('shows Work IQ sources when the toggle is enabled', () => {
    render(
      <SettingsPanel
        settings={DEFAULT_SETTINGS}
        conversationSettings={{ ...DEFAULT_CONVERSATION_SETTINGS, workIqEnabled: true }}
        workIqStatus="ready"
        onChange={() => {}}
        onConversationSettingsChange={() => {}}
        t={t}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Work IQ/ }))

    expect(screen.getByText('会議メモ')).toBeInTheDocument()
    expect(screen.getByText('メール')).toBeInTheDocument()
    expect(screen.getByText('Teams チャット')).toBeInTheDocument()
    expect(screen.getByText('ドキュメント/ノート')).toBeInTheDocument()
  })

  it('shows quality controls for GPT Image 2', () => {
    const onChange = vi.fn()
    render(
      <SettingsPanel
        settings={DEFAULT_SETTINGS}
        conversationSettings={DEFAULT_CONVERSATION_SETTINGS}
        workIqStatus="off"
        onChange={onChange}
        onConversationSettingsChange={() => {}}
        t={t}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /画像生成設定/ }))
    fireEvent.change(screen.getByLabelText('画像モデル'), { target: { value: 'gpt-image-2' } })

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ imageModel: 'gpt-image-2' }))
    expect(screen.getByLabelText('画質')).toBeInTheDocument()
    expect(screen.queryByLabelText('幅 (px)')).toBeNull()
  })

  it('disables the Work IQ toggle when the conversation is locked', () => {
    render(
      <SettingsPanel
        settings={DEFAULT_SETTINGS}
        conversationSettings={DEFAULT_CONVERSATION_SETTINGS}
        workIqStatus="sign_in_required"
        onChange={() => {}}
        onConversationSettingsChange={() => {}}
        workIqLocked
        t={t}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Work IQ/ }))

    expect(screen.getByRole('checkbox')).toBeDisabled()
    expect(screen.getByText('新しい会話を開始してください')).toBeInTheDocument()
    expect(screen.getByText('サインインが必要')).toBeInTheDocument()
  })
})
