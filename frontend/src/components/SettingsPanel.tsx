/**
 * モデル設定パネル。Temperature / Max Tokens / Top P / Foundry IQ パラメータを調整する。
 */

import { Building2, ChevronDown, ImagePlus, ShieldCheck, SlidersHorizontal } from 'lucide-react'
import { useState } from 'react'

export interface ModelSettings {
  model: string
  marketingPlanRuntime: 'legacy' | 'foundry_preprovisioned'
  workIqRuntime?: 'graph_prefetch' | 'foundry_tool'
  temperature: number
  maxTokens: number
  topP: number
  iqSearchResults: number
  iqScoreThreshold: number
  imageModel: string
  imageQuality: string
  imageWidth: number
  imageHeight: number
  managerApprovalEnabled: boolean
  managerEmail: string
}

export type WorkIqSourceScope = 'meeting_notes' | 'emails' | 'teams_chats' | 'documents_notes'
export type WorkIqUiStatus = 'off' | 'ready' | 'enabled' | 'sign_in_required' | 'consent_required' | 'unavailable'

export interface ConversationSettings {
  workIqEnabled: boolean
  workIqSourceScope: WorkIqSourceScope[]
}

// eslint-disable-next-line react-refresh/only-export-components
export const DEFAULT_SETTINGS: ModelSettings = {
  model: 'gpt-5-4-mini',
  marketingPlanRuntime: 'foundry_preprovisioned',
  workIqRuntime: 'foundry_tool',
  temperature: 0.7,
  maxTokens: 16384,
  topP: 1.0,
  iqSearchResults: 5,
  iqScoreThreshold: 0.0,
  imageModel: 'gpt-image-1.5',
  imageQuality: 'medium',
  imageWidth: 1024,
  imageHeight: 1024,
  managerApprovalEnabled: false,
  managerEmail: '',
}

// eslint-disable-next-line react-refresh/only-export-components
export const DEFAULT_WORKIQ_SOURCE_SCOPE: WorkIqSourceScope[] = [
  'meeting_notes',
  'emails',
  'teams_chats',
  'documents_notes',
]

// eslint-disable-next-line react-refresh/only-export-components
export const DEFAULT_CONVERSATION_SETTINGS: ConversationSettings = {
  workIqEnabled: false,
  workIqSourceScope: [...DEFAULT_WORKIQ_SOURCE_SCOPE],
}

const MANAGER_EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const AVAILABLE_MODELS = [
  { value: 'gpt-5-4-mini', label: 'GPT-5.4 mini (default)' },
  { value: 'gpt-5.4', label: 'GPT-5.4' },
  { value: 'gpt-4-1-mini', label: 'GPT-4.1 mini' },
  { value: 'gpt-4.1', label: 'GPT-4.1' },
]

const MARKETING_RUNTIME_OPTIONS = [
  { value: 'legacy', labelKey: 'settings.marketingRuntime.legacy' },
  { value: 'foundry_preprovisioned', labelKey: 'settings.marketingRuntime.foundry_preprovisioned' },
] as const

const AVAILABLE_IMAGE_MODELS = [
  { value: 'gpt-image-1.5', label: 'GPT Image 1.5 (default)' },
  { value: 'MAI-Image-2', label: 'MAI-Image-2' },
]

const IMAGE_QUALITY_OPTIONS = [
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
]

// eslint-disable-next-line react-refresh/only-export-components
export function normalizeMarketingPlanRuntime(
  runtime: string | null | undefined,
): ModelSettings['marketingPlanRuntime'] {
  return runtime === 'legacy' ? 'legacy' : 'foundry_preprovisioned'
}

// eslint-disable-next-line react-refresh/only-export-components
export function normalizeWorkIqRuntime(
  runtime: string | null | undefined,
): NonNullable<ModelSettings['workIqRuntime']> {
  return runtime === 'graph_prefetch' ? 'graph_prefetch' : 'foundry_tool'
}

// eslint-disable-next-line react-refresh/only-export-components
export function normalizeModelSettings(settings: ModelSettings): ModelSettings {
  return {
    ...settings,
    marketingPlanRuntime: normalizeMarketingPlanRuntime(settings.marketingPlanRuntime),
    workIqRuntime: normalizeWorkIqRuntime(settings.workIqRuntime),
  }
}

interface SettingsPanelProps {
  settings: ModelSettings
  conversationSettings: ConversationSettings
  workIqStatus: WorkIqUiStatus
  onChange: (settings: ModelSettings) => void
  onConversationSettingsChange: (settings: ConversationSettings) => void
  workIqLocked?: boolean
  t: (key: string) => string
}

type SettingsSection = 'model' | 'image' | 'manager' | 'workiq'

interface SliderFieldProps {
  inputId: string
  label: string
  tooltip: string
  value: number
  min: number
  max: number
  step: number
  onChange: (value: number) => void
}

function SliderField({ inputId, label, tooltip, value, min, max, step, onChange }: SliderFieldProps) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <label htmlFor={inputId} className="text-xs font-medium text-[var(--text-secondary)]" title={tooltip}>
          {label}
          <span className="ml-1 cursor-help text-[var(--text-muted)]" title={tooltip}>ⓘ</span>
        </label>
        <span className="rounded bg-[var(--panel-strong)] px-2 py-0.5 text-xs font-mono text-[var(--text-primary)]">
          {value}
        </span>
      </div>
      <input
        id={inputId}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full accent-[var(--accent-strong)] h-1.5 cursor-pointer appearance-none rounded-full bg-[var(--panel-border)]
          [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:appearance-none
          [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-[var(--accent-strong)]
          [&::-webkit-slider-thumb]:shadow-sm [&::-webkit-slider-thumb]:transition-transform
          [&::-webkit-slider-thumb]:hover:scale-110"
      />
    </div>
  )
}

const WORKIQ_STATUS_STYLES: Record<WorkIqUiStatus, string> = {
  off: 'border-[var(--panel-border)] bg-[var(--panel-strong)] text-[var(--text-secondary)]',
  ready: 'border-[var(--accent)]/20 bg-[var(--accent-soft)] text-[var(--accent-strong)]',
  enabled: 'border-emerald-300/70 bg-emerald-100/80 text-emerald-800 dark:border-emerald-700/60 dark:bg-emerald-950/40 dark:text-emerald-200',
  sign_in_required: 'border-amber-300/80 bg-amber-100/80 text-amber-800 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-200',
  consent_required: 'border-violet-300/80 bg-violet-100/80 text-violet-800 dark:border-violet-700/60 dark:bg-violet-950/40 dark:text-violet-200',
  unavailable: 'border-rose-300/80 bg-rose-100/80 text-rose-800 dark:border-rose-700/60 dark:bg-rose-950/40 dark:text-rose-200',
}

const WORKIQ_STATUS_MESSAGE_KEYS: Record<WorkIqUiStatus, string> = {
  off: 'settings.workiq.message.off',
  ready: 'settings.workiq.message.ready',
  enabled: 'settings.workiq.message.enabled',
  sign_in_required: 'settings.workiq.message.sign_in_required',
  consent_required: 'settings.workiq.message.consent_required',
  unavailable: 'settings.workiq.message.unavailable',
}

export function SettingsPanel({
  settings,
  conversationSettings,
  workIqStatus,
  onChange,
  onConversationSettingsChange,
  workIqLocked = false,
  t,
}: SettingsPanelProps) {
  const [activeSection, setActiveSection] = useState<SettingsSection | null>(null)
  const trimmedManagerEmail = settings.managerEmail.trim()
  const isManagerEmailInvalid = settings.managerApprovalEnabled
    && trimmedManagerEmail.length > 0
    && !MANAGER_EMAIL_PATTERN.test(trimmedManagerEmail)
  const isWorkIqEnabled = conversationSettings.workIqEnabled
  const isResetDisabled = activeSection === 'workiq' && workIqLocked
  const workIqStatusLabel = t(`settings.workiq.status.${workIqStatus}`)
  const workIqMessage = t(WORKIQ_STATUS_MESSAGE_KEYS[workIqStatus])

  const sectionOptions: Array<{ key: SettingsSection; label: string; Icon: typeof SlidersHorizontal }> = [
    { key: 'model', label: t('settings.title'), Icon: SlidersHorizontal },
    { key: 'image', label: t('settings.image.title'), Icon: ImagePlus },
    { key: 'manager', label: t('settings.manager.title'), Icon: ShieldCheck },
    { key: 'workiq', label: t('settings.workiq.title'), Icon: Building2 },
  ]

  const update = (key: keyof ModelSettings, value: number | string | boolean) => {
    const nextSettings = { ...settings, [key]: value } as ModelSettings
    onChange(normalizeModelSettings(nextSettings))
  }

  const resetSectionDefaults = (section: SettingsSection) => {
    if (section === 'model') {
        onChange({
          ...settings,
          model: DEFAULT_SETTINGS.model,
          marketingPlanRuntime: DEFAULT_SETTINGS.marketingPlanRuntime,
          temperature: DEFAULT_SETTINGS.temperature,
          maxTokens: DEFAULT_SETTINGS.maxTokens,
          topP: DEFAULT_SETTINGS.topP,
        iqSearchResults: DEFAULT_SETTINGS.iqSearchResults,
        iqScoreThreshold: DEFAULT_SETTINGS.iqScoreThreshold,
      })
      return
    }

    if (section === 'image') {
      onChange({
        ...settings,
        imageModel: DEFAULT_SETTINGS.imageModel,
        imageQuality: DEFAULT_SETTINGS.imageQuality,
        imageWidth: DEFAULT_SETTINGS.imageWidth,
        imageHeight: DEFAULT_SETTINGS.imageHeight,
      })
      return
    }

    if (section === 'workiq') {
      if (workIqLocked) return
      onConversationSettingsChange({
        workIqEnabled: DEFAULT_CONVERSATION_SETTINGS.workIqEnabled,
        workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
      })
      return
    }

    onChange({
      ...settings,
      managerApprovalEnabled: DEFAULT_SETTINGS.managerApprovalEnabled,
      managerEmail: DEFAULT_SETTINGS.managerEmail,
    })
  }

  const toggleSection = (section: SettingsSection) => {
    setActiveSection(current => current === section ? null : section)
  }

  return (
    <div className="mb-3">
      <div className="flex flex-wrap gap-2">
        {sectionOptions.map(({ key, label, Icon }) => {
          const isOpen = activeSection === key
          return (
            <button
              key={key}
              type="button"
              onClick={() => toggleSection(key)}
              className={`flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors ${isOpen
                ? 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]'
                : 'border-[var(--panel-border)] text-[var(--text-secondary)] hover:bg-[var(--panel-strong)] hover:text-[var(--text-primary)]'}`}
            >
              <Icon className="h-3.5 w-3.5" />
              <span>{label}</span>
              <ChevronDown className={`h-3.5 w-3.5 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
            </button>
          )
        })}
      </div>

      {activeSection && (
        <div className="mt-2 rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4 shadow-sm">
          <div className="mb-4 flex items-center justify-between gap-3">
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[var(--text-muted)]">
              {sectionOptions.find(section => section.key === activeSection)?.label}
            </p>
            <button
              type="button"
              onClick={() => resetSectionDefaults(activeSection)}
              disabled={isResetDisabled}
              className={`rounded-full border border-[var(--panel-border)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors ${isResetDisabled
                ? 'cursor-not-allowed opacity-50'
                : 'hover:bg-[var(--panel-strong)] hover:text-[var(--text-primary)]'}`}
            >
              {t('settings.reset')}
            </button>
          </div>

          {activeSection === 'model' && (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label htmlFor="settings-model" className="text-xs font-medium text-[var(--text-secondary)]" title={t('settings.model.desc')}>
                    {t('settings.model')}
                    <span className="ml-1 cursor-help text-[var(--text-muted)]" title={t('settings.model.desc')}>ⓘ</span>
                  </label>
                </div>
                <select
                  id="settings-model"
                  value={settings.model}
                  onChange={(e) => update('model', e.target.value)}
                  aria-label={t('settings.model')}
                  className="w-full rounded-md border border-[var(--panel-border)] bg-[var(--panel-strong)] px-2 py-1.5 text-xs font-mono text-[var(--text-primary)] accent-[var(--accent-strong)] cursor-pointer focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)]"
                >
                  {AVAILABLE_MODELS.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label
                    htmlFor="settings-marketing-runtime"
                    className="text-xs font-medium text-[var(--text-secondary)]"
                    title={t('settings.marketingRuntime.desc')}
                  >
                    {t('settings.marketingRuntime')}
                    <span className="ml-1 cursor-help text-[var(--text-muted)]" title={t('settings.marketingRuntime.desc')}>ⓘ</span>
                  </label>
                </div>
                <select
                  id="settings-marketing-runtime"
                  value={normalizeMarketingPlanRuntime(settings.marketingPlanRuntime)}
                  onChange={(e) => update('marketingPlanRuntime', e.target.value)}
                  aria-label={t('settings.marketingRuntime')}
                  className="w-full rounded-md border border-[var(--panel-border)] bg-[var(--panel-strong)] px-2 py-1.5 text-xs font-mono text-[var(--text-primary)] accent-[var(--accent-strong)] cursor-pointer focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)]"
                >
                  {MARKETING_RUNTIME_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{t(option.labelKey)}</option>
                  ))}
                </select>
              </div>
              <SliderField
                inputId="settings-temperature"
                label={t('settings.temperature')}
                tooltip={t('settings.temperature.desc')}
                value={settings.temperature}
                min={0}
                max={2}
                step={0.1}
                onChange={(v) => update('temperature', v)}
              />
              <SliderField
                inputId="settings-max-tokens"
                label={t('settings.maxTokens')}
                tooltip={t('settings.maxTokens')}
                value={settings.maxTokens}
                min={256}
                max={16384}
                step={256}
                onChange={(v) => update('maxTokens', v)}
              />
              <SliderField
                inputId="settings-top-p"
                label={t('settings.topP')}
                tooltip="Top P"
                value={settings.topP}
                min={0}
                max={1}
                step={0.05}
                onChange={(v) => update('topP', v)}
              />
              <SliderField
                inputId="settings-iq-results"
                label={t('settings.iqResults')}
                tooltip={t('settings.iqResults')}
                value={settings.iqSearchResults}
                min={1}
                max={20}
                step={1}
                onChange={(v) => update('iqSearchResults', v)}
              />
              <SliderField
                inputId="settings-iq-threshold"
                label={t('settings.iqThreshold')}
                tooltip={t('settings.iqThreshold')}
                value={settings.iqScoreThreshold}
                min={0}
                max={1}
                step={0.05}
                onChange={(v) => update('iqScoreThreshold', v)}
              />
            </div>
          )}

          {activeSection === 'image' && (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label htmlFor="settings-image-model" className="text-xs font-medium text-[var(--text-secondary)]" title={t('settings.image.model.desc')}>
                    {t('settings.image.model')}
                    <span className="ml-1 cursor-help text-[var(--text-muted)]" title={t('settings.image.model.desc')}>ⓘ</span>
                  </label>
                </div>
                <select
                  id="settings-image-model"
                  value={settings.imageModel}
                  onChange={(e) => update('imageModel', e.target.value)}
                  aria-label={t('settings.image.model')}
                  className="w-full rounded-md border border-[var(--panel-border)] bg-[var(--panel-strong)] px-2 py-1.5 text-xs font-mono text-[var(--text-primary)] accent-[var(--accent-strong)] cursor-pointer focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)]"
                >
                  {AVAILABLE_IMAGE_MODELS.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </div>

              {settings.imageModel === 'gpt-image-1.5' && (
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between">
                    <label htmlFor="settings-image-quality" className="text-xs font-medium text-[var(--text-secondary)]" title={t('settings.image.quality.desc')}>
                      {t('settings.image.quality')}
                      <span className="ml-1 cursor-help text-[var(--text-muted)]" title={t('settings.image.quality.desc')}>ⓘ</span>
                    </label>
                  </div>
                  <select
                    id="settings-image-quality"
                    value={settings.imageQuality}
                    onChange={(e) => update('imageQuality', e.target.value)}
                    aria-label={t('settings.image.quality')}
                    className="w-full rounded-md border border-[var(--panel-border)] bg-[var(--panel-strong)] px-2 py-1.5 text-xs font-mono text-[var(--text-primary)] accent-[var(--accent-strong)] cursor-pointer focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)]"
                  >
                    {IMAGE_QUALITY_OPTIONS.map((q) => (
                      <option key={q.value} value={q.value}>{q.label}</option>
                    ))}
                  </select>
                </div>
              )}

              {settings.imageModel === 'MAI-Image-2' && (
                <>
                  <SliderField
                    inputId="settings-image-width"
                    label={t('settings.image.width')}
                    tooltip={t('settings.image.width.desc')}
                    value={settings.imageWidth}
                    min={768}
                    max={1024}
                    step={16}
                    onChange={(v) => update('imageWidth', v)}
                  />
                  <SliderField
                    inputId="settings-image-height"
                    label={t('settings.image.height')}
                    tooltip={t('settings.image.height.desc')}
                    value={settings.imageHeight}
                    min={768}
                    max={1024}
                    step={16}
                    onChange={(v) => update('imageHeight', v)}
                  />
                </>
              )}
              </div>
              {settings.imageModel === 'MAI-Image-2' && (
                <p className="mt-2 text-[10px] text-[var(--text-muted)]">
                  {t('settings.image.mai.constraint')}
                </p>
              )}
            </>
          )}

          {activeSection === 'manager' && (
            <div className="space-y-3">
              <label
                htmlFor="settings-manager-approval"
                className="flex items-center justify-between rounded-xl border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-2.5"
              >
                <div className="pr-4">
                  <p className="text-xs font-medium text-[var(--text-primary)]">{t('settings.manager.enabled')}</p>
                  <p className="mt-1 text-[11px] text-[var(--text-muted)]">{t('settings.manager.enabled.desc')}</p>
                </div>
                <input
                  id="settings-manager-approval"
                  type="checkbox"
                  checked={settings.managerApprovalEnabled}
                  onChange={(e) => update('managerApprovalEnabled', e.target.checked)}
                  className="h-4 w-4 rounded border-[var(--panel-border)] text-[var(--accent-strong)] focus:ring-[var(--accent-strong)]"
                />
              </label>

              {settings.managerApprovalEnabled && (
                <div className="space-y-1.5">
                  <label
                    htmlFor="settings-manager-email"
                    className="text-xs font-medium text-[var(--text-secondary)]"
                    title={t('settings.manager.email.desc')}
                  >
                    {t('settings.manager.email')}
                    <span className="ml-1 cursor-help text-[var(--text-muted)]" title={t('settings.manager.email.desc')}>ⓘ</span>
                  </label>
                  <input
                    id="settings-manager-email"
                    type="email"
                    value={settings.managerEmail}
                    onChange={(e) => update('managerEmail', e.target.value)}
                    placeholder={t('settings.manager.email.placeholder')}
                    className="w-full rounded-md border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-2 text-xs text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--accent-strong)]"
                  />
                  {isManagerEmailInvalid && (
                    <p className="text-[11px] text-rose-500">{t('settings.manager.email.invalid')}</p>
                  )}
                </div>
              )}
            </div>
          )}

          {activeSection === 'workiq' && (
            <div className="space-y-4">
              <label
                htmlFor="settings-workiq-enabled"
                className={`flex items-center justify-between rounded-xl border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-2.5 ${workIqLocked ? 'opacity-70' : ''}`}
              >
                <div className="pr-4">
                  <p className="text-xs font-medium text-[var(--text-primary)]">{t('settings.workiq.enabled')}</p>
                  <p className="mt-1 text-[11px] text-[var(--text-muted)]">{t('settings.workiq.enabled.desc')}</p>
                </div>
                <input
                  id="settings-workiq-enabled"
                  type="checkbox"
                  checked={isWorkIqEnabled}
                  disabled={workIqLocked}
                  onChange={(e) => onConversationSettingsChange({
                    ...conversationSettings,
                    workIqEnabled: e.target.checked,
                  })}
                  className="h-4 w-4 rounded border-[var(--panel-border)] text-[var(--accent-strong)] focus:ring-[var(--accent-strong)] disabled:cursor-not-allowed disabled:opacity-60"
                />
              </label>

              <div className="rounded-xl border border-[var(--panel-border)] bg-[var(--surface)] px-3 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[11px] font-medium text-[var(--text-muted)]">{t('settings.workiq.status')}</span>
                  <span className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] ${WORKIQ_STATUS_STYLES[workIqStatus]}`}>
                    {workIqStatusLabel}
                  </span>
                </div>
                <p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">
                  {workIqMessage}
                </p>
                {workIqLocked && (
                  <p className="mt-2 text-[11px] text-[var(--text-muted)]">
                    {t('settings.workiq.locked')}
                  </p>
                )}
              </div>

              {isWorkIqEnabled && (
                <div className="space-y-2">
                  <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-[var(--text-muted)]">
                    {t('settings.workiq.sources')}
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {conversationSettings.workIqSourceScope.map((source) => (
                      <span
                        key={source}
                        className="rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] px-2.5 py-1 text-[10px] font-medium text-[var(--text-secondary)]"
                      >
                        {t(`settings.workiq.source.${source}`)}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
