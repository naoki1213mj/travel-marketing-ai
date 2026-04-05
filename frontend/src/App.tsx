import { Download } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ApprovalBanner } from './components/ApprovalBanner'
import { ArtifactTabs } from './components/ArtifactTabs'
import { BrochurePreview } from './components/BrochurePreview'
import { ConversationHistory } from './components/ConversationHistory'
import { EvaluationPanel } from './components/EvaluationPanel'
import { ImageGallery } from './components/ImageGallery'
import { InputForm } from './components/InputForm'
import { LanguageSwitcher } from './components/LanguageSwitcher'
import { ManagerApprovalPage } from './components/ManagerApprovalPage'
import { ManagerApprovalStatus } from './components/ManagerApprovalStatus'
import { MarkdownView } from './components/MarkdownView'
import { PdfUpload } from './components/PdfUpload'
import { PipelineStepper } from './components/PipelineStepper'
import { PlanVersionTabs } from './components/PlanVersionTabs'
import { RefineChat } from './components/RefineChat'
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
import { isApprovalResponseText, shouldHidePlanDuringPostApprovalRevision } from './lib/approval-flow'
import { buildEvaluationQuery } from './lib/evaluation'
import { exportAllAsJson, exportBrochureHtml, exportPlanMarkdown } from './lib/export'
import { buildPlanVersions } from './lib/plan-versions'


const AGENT_STEP_KEY: Record<string, string> = {
  'data-search-agent': 'step.data_search',
  'marketing-plan-agent': 'step.marketing_plan',
  'approval': 'step.approval',
  'regulation-check-agent': 'step.regulation',
  'plan-revision-agent': 'step.plan_revision',
  'brochure-gen-agent': 'step.brochure',
  'video-gen-agent': 'step.video',
}

function App() {
  const { state, sendMessage, approve, reset, restoreVersion, updateSettings, restoreConversation, saveEvaluation } = useSSE()
  const { theme, setTheme } = useTheme()
  const { locale, setLocale, t } = useI18n()
  const [managerPortalRequest] = useState(() => {
    const searchParams = new URLSearchParams(window.location.search)
    const hashParams = new URLSearchParams(window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash)
    const conversationId = searchParams.get('manager_conversation_id')?.trim() || ''
    const approvalToken = hashParams.get('manager_approval_token')?.trim() || ''
    return conversationId && approvalToken
      ? { conversationId, approvalToken }
      : null
  })

  // 音声入力テキスト — InputForm に挿入して確認後に送信
  const [voiceDraft, setVoiceDraft] = useState({ id: 0, text: '' })
  const [revisionInProgress, setRevisionInProgress] = useState(false)
  const [pendingPreviewSelection, setPendingPreviewSelection] = useState<{
    pendingVersion: number
    committedVersion: number | null
  } | null>(null)

  // Esc キーでリセット
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && state.status === 'completed') reset()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [state.status, reset])

  const isRunning = state.status === 'running'
  const isCompleted = state.status === 'completed'
  const elapsed = useElapsedTime(isRunning, state.agentProgress?.step ?? 0)
  const selectedPendingPreviewVersion = state.pendingVersion
    && pendingPreviewSelection?.pendingVersion === state.pendingVersion.version
    ? pendingPreviewSelection.committedVersion
    : null
  const previewSnapshot = selectedPendingPreviewVersion && state.pendingVersion
    ? state.versions[selectedPendingPreviewVersion - 1] ?? null
    : null
  const isViewingCommittedPreview = Boolean(previewSnapshot)
  const previewVersionNumber = isViewingCommittedPreview
    ? selectedPendingPreviewVersion
    : (state.pendingVersion?.version ?? state.currentVersion)
  const previewTextContents = previewSnapshot
    ? previewSnapshot.textContents
    : state.pendingVersion
      ? state.textContents.slice(state.pendingVersion.textOffset)
      : state.textContents
  const previewImages = previewSnapshot
    ? previewSnapshot.images
    : state.pendingVersion
      ? state.images.slice(state.pendingVersion.imageOffset)
      : state.images
  const workflowTextContents = previewSnapshot
    ? previewSnapshot.textContents
    : state.textContents
  const workflowToolEvents = previewSnapshot
    ? previewSnapshot.toolEvents
    : state.toolEvents
  const workflowMetrics = previewSnapshot
    ? previewSnapshot.metrics
    : state.metrics
  const workflowAgentProgress = previewSnapshot ? null : state.agentProgress
  const workflowError = previewSnapshot ? null : state.error
  const planContent = previewTextContents.findLast(c => c.agent === 'marketing-plan-agent')
  const revisionContent = previewTextContents.findLast(c => c.agent === 'plan-revision-agent')
  const planVersions = buildPlanVersions(previewTextContents)
  const [selectedPlanVersionIndexes, setSelectedPlanVersionIndexes] = useState<Record<string, number>>({})
  const statusLabel = t(`status.${state.status}`)
  const pendingPlanLabel = state.pendingVersion
    ? t('version.generating').replace('{n}', String(state.pendingVersion.version))
    : state.status === 'approval'
    ? t('status.approval')
    : state.agentProgress?.agent === 'plan-revision-agent'
      ? t('step.plan_revision')
      : t('step.regulation')
  const planVersionScope = `${state.conversationId ?? 'draft'}:${previewVersionNumber || 'draft'}`
  const defaultPlanVersionIndex = Math.max(planVersions.length - 1, 0)
  const activePlanVersionIndex = Math.min(
    selectedPlanVersionIndexes[planVersionScope] ?? defaultPlanVersionIndex,
    defaultPlanVersionIndex,
  )
  const activePlanVersion = planVersions[activePlanVersionIndex]
  const currentSnapshot = state.currentVersion > 0
    ? state.versions[state.currentVersion - 1]
    : null
  const hasRegulationStageStarted = state.agentProgress?.agent === 'regulation-check-agent'
    || state.agentProgress?.agent === 'plan-revision-agent'
    || previewTextContents.some(c => c.agent === 'regulation-check-agent')
  const shouldHidePlan = shouldHidePlanDuringPostApprovalRevision({
    status: state.status,
    hasApprovalRequest: Boolean(state.approvalRequest),
    hasRevisionContent: Boolean(revisionContent?.content),
    hasRegulationStageStarted,
  })
  const displayedPlan = shouldHidePlan
    ? ''
    : revisionContent?.content
    || activePlanVersion?.content
    || state.approvalRequest?.plan_markdown
    || planContent?.content
    || ''
  const evaluationQuery = buildEvaluationQuery(state.userMessages)
  const evaluationVersion = displayedPlan
    ? (isViewingCommittedPreview
        ? selectedPendingPreviewVersion ?? undefined
        : !state.pendingVersion
          ? (state.currentVersion || 1)
          : undefined)
    : undefined
  const evaluationSnapshot = evaluationVersion
    ? state.versions[evaluationVersion - 1] ?? null
    : null
  const latestCommittedVersion = evaluationVersion ? Math.max(state.versions.length, evaluationVersion) : state.versions.length
  const showEvaluationPanel = Boolean(displayedPlan && (isViewingCommittedPreview || !state.pendingVersion))
  const showDraftPlanTabs = !revisionContent && planVersions.length > 1
  const showRevisionNotice = revisionInProgress && state.status === 'running'
  const previewHtml = previewTextContents.findLast(c => c.content_type === 'html')?.content || ''
  const previewVideoUrl = previewTextContents.findLast(c => c.content_type === 'video')?.content
  const previewVideoStatus = previewTextContents.findLast(c => c.agent === 'video-gen-agent' && c.content_type !== 'video')?.content
  const isManagerApproval = state.approvalRequest?.approval_scope === 'manager'
  const showManagerApprovalPhase = state.hasManagerApprovalPhase
  const isManagerApprovalStepActive = state.status === 'approval' && isManagerApproval
  const shouldPollConversationUpdates = Boolean(state.conversationId)
    && (state.managerApprovalPolling || state.backgroundUpdatesPending)
  const workflowHeaderTags = [
    t('panel.workflow.hint.progress'),
    t('panel.workflow.hint.approval'),
    t('panel.workflow.hint.rounds'),
  ]
  const previewHeaderTags = [
    t('panel.preview.hint.version'),
    t('panel.preview.hint.plan'),
    t('panel.preview.hint.assets'),
  ]

  useEffect(() => {
    if (managerPortalRequest) return
    if (!shouldPollConversationUpdates || !state.conversationId) return

    const intervalId = window.setInterval(() => {
      void restoreConversation(state.conversationId || '')
    }, 5000)

    return () => window.clearInterval(intervalId)
  }, [managerPortalRequest, restoreConversation, shouldPollConversationUpdates, state.conversationId])

  const pendingVersionNotice = state.pendingVersion ? (
    <div className="mb-3 rounded-2xl border border-[var(--accent)]/20 bg-[var(--accent-soft)] px-4 py-3 text-sm text-[var(--accent-strong)]">
      <div className="flex items-center gap-2 font-medium">
        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-[var(--accent)]" />
        {isViewingCommittedPreview
          ? t('preview.pending.viewing_previous')
            .replace('{current}', String(selectedPendingPreviewVersion))
            .replace('{pending}', String(state.pendingVersion.version))
          : t('version.generating').replace('{n}', String(state.pendingVersion.version))}
      </div>
      <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
        {isViewingCommittedPreview
          ? t('preview.pending.return_live').replace('{n}', String(state.pendingVersion.version))
          : t('preview.pending.subtitle')}
      </p>
      {state.versions.length > 0 && (
        <p className="mt-1 text-[11px] text-[var(--text-muted)]">
          {t('preview.pending.previous').replace('{n}', String(state.versions.length))}
        </p>
      )}
    </div>
  ) : null
  const handleSendMessage = (message: string) => {
    setRevisionInProgress(false)
    setPendingPreviewSelection(null)
    void sendMessage(message)
  }
  const handleReset = () => {
    setRevisionInProgress(false)
    setPendingPreviewSelection(null)
    reset()
  }
  const handleRestoreConversation = (conversationId: string) => {
    setRevisionInProgress(false)
    setPendingPreviewSelection(null)
    void restoreConversation(conversationId)
  }
  const handleApproval = (response: string) => {
    const trimmed = response.trim()
    setRevisionInProgress(!isApprovalResponseText(trimmed))
    setPendingPreviewSelection(null)
    void approve(trimmed)
  }
  const handleVersionChange = (version: number) => {
    if (state.pendingVersion) {
      setPendingPreviewSelection({
        pendingVersion: state.pendingVersion.version,
        committedVersion: version,
      })
      return
    }

    setPendingPreviewSelection(null)
    restoreVersion(version)
  }
  const handleSelectPendingVersion = () => {
    if (!state.pendingVersion) return
    setPendingPreviewSelection({
      pendingVersion: state.pendingVersion.version,
      committedVersion: null,
    })
  }

  if (managerPortalRequest) {
    return (
      <div className="min-h-screen bg-[var(--app-bg)] text-[var(--text-primary)]">
        <div className="mx-auto flex min-h-screen max-w-[1120px] flex-col px-4 py-4 sm:px-6 lg:px-8">
          <header className="relative z-20 rounded-full border border-[var(--panel-border)] bg-[var(--panel-bg)] px-6 py-3 shadow-[0_8px_30px_rgba(15,23,42,0.06)] backdrop-blur">
            <div className="flex items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <h1 className="text-lg font-semibold tracking-tight">{t('approval.manager.portal.title')}</h1>
              </div>
              <div className="flex items-center gap-2">
                <LanguageSwitcher locale={locale} onChange={setLocale} t={t} />
                <ThemeToggle theme={theme} onChange={setTheme} t={t} />
              </div>
            </div>
          </header>
          <main className="mt-4 flex-1">
            <ManagerApprovalPage
              conversationId={managerPortalRequest.conversationId}
              approvalToken={managerPortalRequest.approvalToken}
              t={t}
            />
          </main>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-[var(--app-bg)] text-[var(--text-primary)]">
      <div className="mx-auto flex min-h-screen max-w-[1600px] flex-col px-4 py-4 sm:px-6 lg:px-8">
      <header className="relative z-20 rounded-full border border-[var(--panel-border)] bg-[var(--panel-bg)] px-6 py-3 shadow-[0_8px_30px_rgba(15,23,42,0.06)] backdrop-blur">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <h1 className="text-lg font-semibold tracking-tight">{t('app.title')}</h1>
            <span className="rounded-full bg-[var(--accent-soft)] px-2.5 py-0.5 text-[10px] font-medium text-[var(--accent-strong)]">
              {statusLabel}
            </span>
          </div>
          <div className="flex items-center gap-2">
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
            <div className="mt-3 flex flex-wrap gap-2">
              {workflowHeaderTags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] px-2.5 py-1 text-[11px] font-medium text-[var(--text-secondary)]"
                >
                  {tag}
                </span>
              ))}
            </div>
          </div>

          {/* 会話履歴（インラインパネル） */}
          <div className="px-5 pt-3">
            <ConversationHistory onSelect={handleRestoreConversation} t={t} locale={locale} />
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
                <PipelineStepper
                  progress={state.agentProgress}
                  t={t}
                  showManagerApprovalPhase={showManagerApprovalPhase}
                  managerApprovalActive={isManagerApprovalStepActive}
                />
                {isRunning && (
                  <p className="mt-1 text-xs text-[var(--text-muted)]">
                    {elapsed}s
                    {state.agentProgress && state.agentProgress.agent !== 'approval' && ` · ${t(AGENT_STEP_KEY[state.agentProgress.agent] || '')}`}
                    {state.agentProgress?.agent === 'approval' && ` · ${t('status.approval')}`}
                  </p>
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

            {previewSnapshot && selectedPendingPreviewVersion && (
              <div className="rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-strong)] px-4 py-3 text-sm text-[var(--text-secondary)]">
                {t('preview.pending.viewing_previous')
                  .replace('{current}', String(selectedPendingPreviewVersion))
                  .replace('{pending}', String(state.pendingVersion?.version ?? ''))}
              </div>
            )}

            <WorkflowAccordion
              agentProgress={workflowAgentProgress}
              textContents={workflowTextContents}
              toolEvents={workflowToolEvents}
              metrics={workflowMetrics}
              error={workflowError}
              onRetry={handleReset}
              t={t}
              locale={locale}
            />
          </div>

          {/* 承認バナー（スクロール領域内、スティッキー） */}
          {state.status === 'approval' && state.approvalRequest && (
            <div className="px-5 pb-3">
              {isManagerApproval
                ? <ManagerApprovalStatus request={state.approvalRequest} t={t} />
                : <ApprovalBanner request={state.approvalRequest} onApprove={handleApproval} t={t} />}
            </div>
          )}

          <div className="border-t border-[var(--panel-border)] px-5 py-4">
            <div className="mb-3 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-semibold text-[var(--text-primary)]">{t('panel.composer')}</h3>
                <p className="mt-1 text-xs text-[var(--text-muted)]">{t('panel.composer.subtitle')}</p>
              </div>
            </div>
            <SettingsPanel settings={state.settings} onChange={updateSettings} t={t} />
            {state.status === 'approval' ? (
              <div className="rounded-[20px] border border-[var(--warning-border)] bg-[var(--warning-surface)] px-4 py-3 text-sm text-[var(--warning-text)]">
                {isManagerApproval
                  ? t('approval.manager.awaiting_action').replace('{email}', state.approvalRequest?.manager_email || 'manager')
                  : t('approval.awaiting_action')}
              </div>
            ) : (
              <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
                <div className="flex-1">
                  {state.status === 'completed' ? (
                    <RefineChat
                      onSubmit={handleSendMessage}
                      disabled={isRunning}
                      placeholder={t('refine.placeholder')}
                      sendLabel={t('input.send')}
                      label={t('refine.label')}
                    />
                  ) : (
                    <InputForm
                      key={voiceDraft.id}
                      onSubmit={(msg) => { handleSendMessage(msg); setVoiceDraft(prev => ({ ...prev, text: '' })) }}
                      disabled={isRunning}
                      placeholder={t('input.placeholder')}
                      sendLabel={t('input.send')}
                      label={t('input.label')}
                      initialValue={voiceDraft.text}
                      t={t}
                    />
                  )}
                </div>
                <VoiceInput
                  onTranscript={(text) => setVoiceDraft(prev => ({ id: prev.id + 1, text }))}
                  disabled={isRunning}
                  t={t}
                />
                <PdfUpload disabled={isRunning} t={t} />
              </div>
            )}
          </div>
        </section>

        <section className="flex min-h-[0] flex-col rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] shadow-[0_18px_55px_rgba(15,23,42,0.06)] backdrop-blur">
          <div className="border-b border-[var(--panel-border)] px-5 py-4">
            <h2 className="text-sm font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">{t('panel.preview')}</h2>
            <p className="mt-2 text-sm text-[var(--text-secondary)]">{t('panel.preview.subtitle')}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {previewHeaderTags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] px-2.5 py-1 text-[11px] font-medium text-[var(--text-secondary)]"
                >
                  {tag}
                </span>
              ))}
            </div>
          </div>

          <div className="min-h-[0] flex-1 overflow-y-auto px-5 py-5">
          {state.status === 'idle' ? (
            <div className="flex h-full min-h-80 items-center justify-center rounded-[24px] border border-dashed border-[var(--panel-border)] bg-[var(--panel-strong)] px-8 py-12">
              <div className="max-w-sm text-center">
                <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-[var(--accent-soft)]">
                  <div className="h-3 w-3 animate-pulse rounded-full bg-[var(--accent)]" />
                </div>
                <h3 className="text-xl font-semibold tracking-tight">{t('preview.empty.title')}</h3>
                <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{t('preview.empty.subtitle')}</p>
              </div>
            </div>
          ) : (
            <>
            {(state.versions.length > 1 || state.pendingVersion) && (
              <div className="mb-3 flex items-center justify-center">
                <VersionSelector
                  versions={state.versions.map((_, i) => i + 1)}
                  current={selectedPendingPreviewVersion ?? state.currentVersion}
                  onChange={handleVersionChange}
                  t={t}
                  pendingVersion={state.pendingVersion?.version}
                  viewingPending={Boolean(state.pendingVersion) && !selectedPendingPreviewVersion}
                  onSelectPending={handleSelectPendingVersion}
                />
              </div>
            )}
            <ArtifactTabs tabs={[
              {
                key: 'plan',
                label: t('tab.plan'),
                content: displayedPlan ? (
                  <>
                    {pendingVersionNotice}
                    {showRevisionNotice && (
                      <div className="mb-3 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-100">
                        {t('approval.revision_running')}
                      </div>
                    )}
                    {showDraftPlanTabs && (
                      <PlanVersionTabs
                        versions={planVersions}
                        activeIndex={activePlanVersionIndex}
                        onChangeIndex={(index) => {
                          setSelectedPlanVersionIndexes(prev => ({
                            ...prev,
                            [planVersionScope]: index,
                          }))
                        }}
                      />
                    )}
                    <MarkdownView content={displayedPlan} />
                    {!state.pendingVersion && (
                      <button
                        onClick={() => exportPlanMarkdown(state.textContents)}
                        className="mt-3 inline-flex items-center gap-1 rounded-full border border-[var(--panel-border)] px-4 py-2 text-sm text-[var(--text-secondary)] hover:bg-[var(--accent-soft)] transition-colors"
                      >
                        <Download className="h-3.5 w-3.5" /> {t('export.plan')}
                      </button>
                    )}
                  </>
                ) : (
                  <>
                    {pendingVersionNotice}
                    <div className="flex flex-col items-center justify-center py-12 text-[var(--text-muted)]">
                      <div className="mb-3 h-6 w-6 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
                      <p className="text-sm">{pendingPlanLabel}…</p>
                      {shouldHidePlan ? (
                        <p className="mt-2 max-w-sm text-center text-xs leading-5 text-[var(--text-muted)]">
                          {t('plan.awaiting_revision')}
                        </p>
                      ) : state.pendingVersion ? (
                        <p className="mt-2 max-w-sm text-center text-xs leading-5 text-[var(--text-muted)]">
                          {t('preview.pending.waiting').replace('{n}', String(state.pendingVersion.version))}
                        </p>
                      ) : null}
                    </div>
                  </>
                ),
              },
              {
                key: 'evaluation',
                label: t('tab.evaluation'),
                content: showEvaluationPanel ? (
                  <>
                    {pendingVersionNotice}
                    <EvaluationPanel
                      query={evaluationQuery}
                      response={displayedPlan}
                      html={previewHtml}
                      conversationId={state.conversationId}
                      artifactVersion={evaluationVersion}
                      versions={state.versions}
                      evaluations={evaluationSnapshot?.evaluations ?? currentSnapshot?.evaluations ?? []}
                      isLatestVersion={Boolean(evaluationVersion) && evaluationVersion === latestCommittedVersion}
                      onEvaluationRecorded={saveEvaluation}
                      t={t}
                      onRefine={state.status !== 'approval' ? handleSendMessage : undefined}
                    />
                  </>
                ) : (
                  <div className="rounded-[24px] border border-dashed border-[var(--panel-border)] bg-[var(--panel-strong)] px-6 py-10 text-center">
                    <p className="text-sm font-medium text-[var(--text-primary)]">{t('tab.evaluation')}</p>
                    <p className="mt-2 text-sm text-[var(--text-secondary)]">
                      {state.pendingVersion
                        ? t('eval.pending.latest_only')
                        : t('eval.empty')}
                    </p>
                  </div>
                ),
              },
              { key: 'brochure', label: t('tab.brochure'), content: <BrochurePreview contents={previewTextContents} t={t} /> },
              { key: 'images', label: t('tab.images'), content: <ImageGallery images={previewImages} t={t} /> },
              { key: 'video', label: t('tab.video') || '動画', content: (
                <VideoPreview
                  videoUrl={previewVideoUrl}
                  statusMessage={previewVideoStatus}
                  backgroundPending={state.backgroundUpdatesPending}
                  t={t}
                />
              )},
            ]} t={t} activeAgent={state.agentProgress?.agent} />

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
