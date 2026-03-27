import { AnalysisView } from './components/AnalysisView'
import { ArtifactTabs } from './components/ArtifactTabs'
import { BrochurePreview } from './components/BrochurePreview'
import { ErrorRetry } from './components/ErrorRetry'
import { ImageGallery } from './components/ImageGallery'
import { InputForm } from './components/InputForm'
import { LanguageSwitcher } from './components/LanguageSwitcher'
import { MetricsBar } from './components/MetricsBar'
import { PipelineStepper } from './components/PipelineStepper'
import { PlanApproval } from './components/PlanApproval'
import { RefineChat } from './components/RefineChat'
import { RegulationResults } from './components/RegulationResults'
import { SafetyBadge } from './components/SafetyBadge'
import { ThemeToggle } from './components/ThemeToggle'
import { ToolEventBadges } from './components/ToolEventBadges'
import { useI18n } from './hooks/useI18n'
import { useSSE } from './hooks/useSSE'
import { useTheme } from './hooks/useTheme'

function App() {
  const { state, sendMessage, approve, reset } = useSSE()
  const { theme, setTheme } = useTheme()
  const { locale, setLocale, t } = useI18n()

  const isRunning = state.status === 'running'
  const planContent = state.textContents.find(c => c.agent === 'marketing-plan-agent')

  return (
    <div className="flex min-h-screen flex-col bg-gray-50 text-gray-900 dark:bg-gray-950 dark:text-gray-100">
      {/* ヘッダー */}
      <header className="flex items-center justify-between border-b border-gray-200 px-6 py-3 dark:border-gray-800">
        <div>
          <h1 className="text-lg font-semibold">✈️ {t('app.title')}</h1>
          <p className="text-xs text-gray-500 dark:text-gray-400">{t('app.subtitle')}</p>
        </div>
        <div className="flex items-center gap-3">
          <SafetyBadge result={state.safetyResult} t={t} />
          <LanguageSwitcher locale={locale} onChange={setLocale} />
          <ThemeToggle theme={theme} onChange={setTheme} />
        </div>
      </header>

      {/* メイン: 2カラム */}
      <main className="flex flex-1 overflow-hidden">
        {/* 左カラム: チャット */}
        <div className="flex w-1/2 flex-col border-r border-gray-200 dark:border-gray-800">
          <div className="flex-1 overflow-y-auto p-6 space-y-4">
            {state.status !== 'idle' && (
              <PipelineStepper progress={state.agentProgress} t={t} />
            )}
            <ToolEventBadges events={state.toolEvents} />
            <AnalysisView contents={state.textContents} />

            {planContent && (
              <div className="rounded-lg bg-white p-4 dark:bg-gray-900">
                <div className="mb-2">
                  <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700 dark:bg-blue-900 dark:text-blue-300">
                    📝 施策生成エージェント
                  </span>
                </div>
                <div className="prose prose-sm max-w-none dark:prose-invert">
                  {planContent.content.split('\n').map((line, i) => {
                    if (line.startsWith('# ')) return <h2 key={i}>{line.slice(2)}</h2>
                    if (line.startsWith('## ')) return <h3 key={i}>{line.slice(3)}</h3>
                    if (line.startsWith('- ')) return <li key={i}>{line.slice(2)}</li>
                    if (line.trim()) return <p key={i}>{line}</p>
                    return <br key={i} />
                  })}
                </div>
              </div>
            )}

            {state.status === 'approval' && state.approvalRequest && (
              <PlanApproval request={state.approvalRequest} onApprove={approve} t={t} />
            )}
            <RegulationResults contents={state.textContents} />
            {state.error && (
              <ErrorRetry error={state.error} onRetry={reset} retryLabel={t('error.retry')} />
            )}
            <MetricsBar metrics={state.metrics} t={t} />
          </div>

          <div className="border-t border-gray-200 p-4 dark:border-gray-800">
            {state.status === 'completed' ? (
              <RefineChat onSubmit={sendMessage} disabled={isRunning} placeholder={t('refine.placeholder')} sendLabel={t('input.send')} />
            ) : (
              <InputForm onSubmit={sendMessage} disabled={isRunning} placeholder={t('input.placeholder')} sendLabel={t('input.send')} />
            )}
          </div>
        </div>

        {/* 右カラム: 成果物プレビュー */}
        <div className="flex w-1/2 flex-col overflow-y-auto p-6">
          {state.status === 'idle' ? (
            <div className="flex flex-1 items-center justify-center">
              <div className="text-center">
                <p className="text-5xl">✈️</p>
                <p className="mt-4 text-sm text-gray-400 dark:text-gray-500">{t('input.placeholder')}</p>
              </div>
            </div>
          ) : (
            <ArtifactTabs tabs={[
              {
                key: 'plan',
                label: `📝 ${t('tab.plan')}`,
                content: planContent ? (
                  <div className="prose prose-sm max-w-none dark:prose-invert">
                    {planContent.content.split('\n').map((line, i) => {
                      if (line.startsWith('# ')) return <h2 key={i}>{line.slice(2)}</h2>
                      if (line.startsWith('## ')) return <h3 key={i}>{line.slice(3)}</h3>
                      if (line.startsWith('- ')) return <li key={i}>{line.slice(2)}</li>
                      if (line.trim()) return <p key={i}>{line}</p>
                      return <br key={i} />
                    })}
                  </div>
                ) : null,
              },
              { key: 'brochure', label: `🎨 ${t('tab.brochure')}`, content: <BrochurePreview contents={state.textContents} /> },
              { key: 'images', label: `🖼️ ${t('tab.images')}`, content: <ImageGallery images={state.images} /> },
            ]} />
          )}
        </div>
      </main>
    </div>
  )
}

export default App
