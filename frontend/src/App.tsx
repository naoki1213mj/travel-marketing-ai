import { useState } from 'react'
import { ApprovalBanner } from './components/ApprovalBanner'
import { ArtifactTabs } from './components/ArtifactTabs'
import { BrochurePreview } from './components/BrochurePreview'
import { ConversationHistory } from './components/ConversationHistory'
import { EvaluationPanel } from './components/EvaluationPanel'
import { ImageGallery } from './components/ImageGallery'
import { InputForm } from './components/InputForm'
import { LanguageSwitcher } from './components/LanguageSwitcher'
import { MarkdownView } from './components/MarkdownView'
import { PdfUpload } from './components/PdfUpload'
import { PipelineStepper } from './components/PipelineStepper'
import { RefineChat } from './components/RefineChat'
import { SafetyBadge } from './components/SafetyBadge'
import { SettingsPanel } from './components/SettingsPanel'
import { ThemeToggle } from './components/ThemeToggle'
import { VersionSelector } from './components/VersionSelector'
import { VideoPreview } from './components/VideoPreview'
import { VoiceInput } from './components/VoiceInput'
import { WorkflowAccordion } from './components/WorkflowAccordion'
import { useElapsedTime } from './hooks/useElapsedTime'
import { useI18n } from './hooks/useI18n'
import { useSSE } from './hooks/useSSE'
import { useTheme } from './hooks/useTheme'
import { exportAllAsJson, exportBrochureHtml, exportPlanMarkdown } from './lib/export'


const AGENT_STEP_KEY: Record<string, string> = {
  'data-search-agent': 'step.data_search',
  'marketing-plan-agent': 'step.marketing_plan',
  'approval': 'step.approval',
  'regulation-check-agent': 'step.regulation',
  'plan-revision-agent': 'step.regulation',
  'brochure-gen-agent': 'step.brochure',
  'video-gen-agent': 'step.brochure',
}

function App() {
  const { state, sendMessage, approve, reset, restoreVersion, updateSettings, restoreConversation } = useSSE()
  const { theme, setTheme } = useTheme()
  const { locale, setLocale, t } = useI18n()

  // 音声入力テキスト — InputForm に挿入して確認後に送信
  const [voiceText, setVoiceText] = useState('')


  const isRunning = state.status === 'running'
  const isCompleted = state.status === 'completed'
  const elapsed = useElapsedTime(isRunning, state.agentProgress?.step ?? 0)
  const planContent = state.textContents.findLast(c => c.agent === 'marketing-plan-agent')
  const revisionContent = state.textContents.findLast(c => c.agent === 'plan-revision-agent')
  // revision agent が修正済み企画書を出力するまでは「確認中」として表示
  const showFinalPlan = revisionContent || isCompleted
  const statusLabel = t(`status.${state.status}`)

  return (
    <div className="min-h-screen bg-[var(--app-bg)] text-[var(--text-primary)]">
      <div className="mx-auto flex min-h-screen max-w-[1600px] flex-col px-4 py-4 sm:px-6 lg:px-8">
      <header className="relative z-20 rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] px-5 py-5 shadow-[0_18px_55px_rgba(15,23,42,0.08)] backdrop-blur">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
          <div className="space-y-2">
            <p className="text-[11px] font-semibold uppercase tracking-[0.28em] text-[var(--text-muted)]">{t('app.kicker')}</p>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{t('app.title')}</h1>
              <span className="rounded-full bg-[var(--accent-soft)] px-3 py-1 text-xs font-medium text-[var(--accent-strong)]">
                {statusLabel}
              </span>
            </div>
            <p className="max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">{t('app.subtitle')}</p>
          </div>
          <div className="flex flex-wrap items-center gap-3">
          <SafetyBadge result={state.safetyResult} t={t} />
          <LanguageSwitcher locale={locale} onChange={setLocale} t={t} />
          <ThemeToggle theme={theme} onChange={setTheme} t={t} />
          </div>
        </div>
      </header>

      <main className="mt-4 grid flex-1 gap-4 xl:grid-cols-[minmax(0,1.08fr)_minmax(360px,0.92fr)]">
        <section className="flex min-h-[0] flex-col rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] shadow-[0_18px_55px_rgba(15,23,42,0.06)] backdrop-blur">
          <div className="border-b border-[var(--panel-border)] px-5 py-4">
            <h2 className="text-sm font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">{t('panel.workflow')}</h2>
            <p className="mt-2 text-sm text-[var(--text-secondary)]">{t('panel.workflow.subtitle')}</p>
          </div>

          {/* 会話履歴（インラインパネル） */}
          <div className="px-5 pt-3">
            <ConversationHistory onSelect={restoreConversation} t={t} />
          </div>

          <div className="min-h-[0] flex-1 overflow-y-auto px-5 py-5 space-y-5">
            {/* ユーザーメッセージの会話表示 */}
            {state.userMessages.length > 0 && (
              <div className="space-y-3">
                {state.userMessages.map((msg, i) => (
                  <div key={i} className="flex justify-end">
                    <div className="max-w-[85%] rounded-[20px] rounded-br-md bg-[var(--user-bubble-bg)] px-4 py-3 text-sm text-[var(--user-bubble-text)] shadow-sm">
                      <p className="whitespace-pre-wrap">{msg}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {state.status !== 'idle' && (
              <>
                <PipelineStepper progress={state.agentProgress} t={t} />
                {isRunning && (
                  <div className="mt-2 flex items-center gap-2 text-sm text-[var(--text-muted)]">
                    <span>⏱</span>
                    <span>{elapsed}s</span>
                    {state.agentProgress && state.agentProgress.agent !== 'approval' && (
                      <span>— {t(AGENT_STEP_KEY[state.agentProgress.agent] || '')} {t('status.running')}</span>
                    )}
                    {state.agentProgress?.agent === 'approval' && (
                      <span>— {t('status.approval')}</span>
                    )}
                  </div>
                )}
              </>
            )}

            {/* 処理中のローディング表示 */}
            {isRunning && !state.agentProgress && (
              <div className="flex items-center gap-3 rounded-[20px] border border-[var(--panel-border)] bg-[var(--panel-strong)] px-5 py-4">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
                <span className="text-sm text-[var(--text-secondary)]">{t('status.running')}</span>
              </div>
            )}

            <WorkflowAccordion
              agentProgress={state.agentProgress}
              textContents={state.textContents}
              toolEvents={state.toolEvents}
              metrics={state.metrics}
              error={state.error}
              onRetry={reset}
              t={t}
              locale={locale}
            />
          </div>

          {/* 承認バナー（スクロール領域の外、固定位置） */}
          {state.status === 'approval' && state.approvalRequest && (
            <ApprovalBanner request={state.approvalRequest} onApprove={approve} t={t} />
          )}

          <div className="border-t border-[var(--panel-border)] px-5 py-4">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t('panel.composer')}</h3>
                <p className="mt-1 text-xs text-[var(--text-muted)]">{t('panel.composer.subtitle')}</p>
              </div>
            </div>
            <SettingsPanel settings={state.settings} onChange={updateSettings} t={t} />
            <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
              <div className="flex-1">
                {state.status === 'completed' ? (
                  <RefineChat
                    onSubmit={sendMessage}
                    disabled={isRunning}
                    placeholder={t('refine.placeholder')}
                    sendLabel={t('input.send')}
                    label={t('refine.label')}
                  />
                ) : (
                  <InputForm
                    onSubmit={(msg) => { sendMessage(msg); setVoiceText('') }}
                    disabled={isRunning}
                    placeholder={t('input.placeholder')}
                    sendLabel={t('input.send')}
                    label={t('input.label')}
                    initialValue={voiceText}
                  />
                )}
              </div>
              <VoiceInput onTranscript={setVoiceText} disabled={isRunning} t={t} />
              <PdfUpload disabled={isRunning} t={t} />
            </div>
          </div>
        </section>

        <section className="flex min-h-[0] flex-col rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] shadow-[0_18px_55px_rgba(15,23,42,0.06)] backdrop-blur">
          <div className="border-b border-[var(--panel-border)] px-5 py-4">
            <h2 className="text-sm font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">{t('panel.preview')}</h2>
            <p className="mt-2 text-sm text-[var(--text-secondary)]">{t('panel.preview.subtitle')}</p>
          </div>

          <div className="min-h-[0] flex-1 overflow-y-auto px-5 py-5">
          {state.status === 'idle' ? (
            <div className="flex h-full min-h-80 items-center justify-center rounded-[24px] border border-dashed border-[var(--panel-border)] bg-[var(--panel-strong)] px-8 py-12">
              <div className="max-w-sm text-center">
                <p className="text-xs font-semibold uppercase tracking-[0.28em] text-[var(--text-muted)]">Preview</p>
                <h3 className="mt-4 text-2xl font-semibold tracking-tight">{t('preview.empty.title')}</h3>
                <p className="mt-3 text-sm leading-6 text-[var(--text-secondary)]">{t('preview.empty.subtitle')}</p>
              </div>
            </div>
          ) : (
            <>
            {isCompleted && state.versions.length > 1 && (
              <div className="mb-3 flex items-center justify-center">
                <VersionSelector
                  versions={state.versions.map((_, i) => i + 1)}
                  current={state.currentVersion}
                  onChange={restoreVersion}
                  t={t}
                />
              </div>
            )}
            <ArtifactTabs tabs={[
              {
                key: 'plan',
                label: `📝 ${t('tab.plan')}`,
                content: planContent ? (
                  showFinalPlan ? (
                    <>
                      <MarkdownView content={revisionContent?.content || planContent.content} />
                      <button
                        onClick={() => exportPlanMarkdown(state.textContents)}
                        className="mt-3 inline-flex items-center gap-1 rounded-full border border-[var(--panel-border)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--accent-soft)] transition-colors"
                      >
                        💾 {t('export.plan')}
                      </button>
                      {isCompleted && (
                        <EvaluationPanel
                          query={state.userMessages[0] || ''}
                          response={revisionContent?.content || planContent.content}
                          html={state.textContents.findLast(c => c.content_type === 'html')?.content || ''}
                          t={t}
                          onRefine={sendMessage}
                        />
                      )}
                    </>
                  ) : (
                    <div className="flex flex-col items-center justify-center py-12 text-[var(--text-muted)]">
                      <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent mb-3" />
                      <p className="text-sm">
                        {state.status === 'approval' ? t('status.approval') : t('step.regulation')}…
                      </p>
                      <p className="text-xs mt-1">{t('preview.unavailable')}</p>
                    </div>
                  )
                ) : (
                  <div className="flex flex-col items-center justify-center py-12 text-[var(--text-muted)]">
                    <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent mb-3" />
                    <p className="text-sm">{t('step.marketing_plan')}…</p>
                  </div>
                ),
              },
              { key: 'brochure', label: t('tab.brochure'), content: <BrochurePreview contents={state.textContents} t={t} /> },
              { key: 'images', label: t('tab.images'), content: <ImageGallery images={state.images} t={t} /> },
              { key: 'video', label: `🎬 ${t('tab.video') || '動画'}`, content: (
                <VideoPreview
                  videoUrl={state.textContents.findLast(c => c.content_type === 'video')?.content}
                  t={t}
                />
              )},
            ]} t={t} />

            {isCompleted && (
              <>
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-[var(--panel-border)] pt-4">
                <div className="ml-auto flex gap-2">
                <button
                  onClick={() => exportPlanMarkdown(state.textContents)}
                  className="rounded-full border border-[var(--panel-border)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                >
                  {t('export.plan')}
                </button>
                <button
                  onClick={() => exportBrochureHtml(state.textContents)}
                  className="rounded-full border border-[var(--panel-border)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                >
                  {t('export.brochure')}
                </button>
                <button
                  onClick={() => exportAllAsJson(state.textContents, state.images, state.conversationId)}
                  className="rounded-full border border-[var(--panel-border)] bg-[var(--accent-soft)] px-3 py-1.5 text-xs font-medium text-[var(--accent-strong)] transition-colors hover:opacity-90"
                >
                  {t('export.bundle')}
                </button>
                </div>
              </div>
              </>
            )}
            </>
          )}
          </div>
        </section>
      </main>
      </div>
    </div>
  )
}

export default App
