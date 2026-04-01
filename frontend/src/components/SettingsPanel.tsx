/**
 * モデル設定パネル。Temperature / Max Tokens / Top P / Foundry IQ パラメータを調整する。
 */

import { useState } from 'react'

export interface ModelSettings {
  model: string
  temperature: number
  maxTokens: number
  topP: number
  iqSearchResults: number
  iqScoreThreshold: number
}

// eslint-disable-next-line react-refresh/only-export-components
export const DEFAULT_SETTINGS: ModelSettings = {
  model: 'gpt-5-4-mini',
  temperature: 0.7,
  maxTokens: 16384,
  topP: 1.0,
  iqSearchResults: 5,
  iqScoreThreshold: 0.0,
}

const AVAILABLE_MODELS = [
  { value: 'gpt-5-4-mini', label: 'GPT-5.4 mini (default)' },
  { value: 'gpt-5.4', label: 'GPT-5.4' },
  { value: 'gpt-4-1-mini', label: 'GPT-4.1 mini' },
  { value: 'gpt-4.1', label: 'GPT-4.1' },
]

interface SettingsPanelProps {
  settings: ModelSettings
  onChange: (settings: ModelSettings) => void
  t: (key: string) => string
}

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

export function SettingsPanel({ settings, onChange, t }: SettingsPanelProps) {
  const [isOpen, setIsOpen] = useState(false)

  const update = (key: keyof ModelSettings, value: number | string) => {
    onChange({ ...settings, [key]: value })
  }

  const resetToDefaults = () => {
    onChange({ ...DEFAULT_SETTINGS })
  }

  return (
    <div className="mb-3">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1.5 rounded-full border border-[var(--panel-border)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--panel-strong)] hover:text-[var(--text-primary)]"
      >
        <span>⚙️</span>
        <span>{t('settings.title')}</span>
        <span className={`transition-transform ${isOpen ? 'rotate-180' : ''}`}>▾</span>
      </button>

      {isOpen && (
        <div className="mt-2 rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4 shadow-sm">
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
          <div className="mt-4 flex justify-end">
            <button
              type="button"
              onClick={resetToDefaults}
              className="rounded-full border border-[var(--panel-border)] px-3 py-1.5 text-xs font-medium text-[var(--text-secondary)] transition-colors hover:bg-[var(--panel-strong)] hover:text-[var(--text-primary)]"
            >
              {t('settings.reset')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
