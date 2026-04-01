import type { Locale } from '../lib/i18n';

const LOCALES: { value: Locale; label: string }[] = [
  { value: 'ja', label: '日本語' },
  { value: 'en', label: 'English' },
  { value: 'zh', label: '中文' },
]

interface LanguageSwitcherProps {
  locale: Locale
  onChange: (locale: Locale) => void
  t: (key: string) => string
}

export function LanguageSwitcher({ locale, onChange, t }: LanguageSwitcherProps) {
  return (
    <label className="flex items-center gap-2 rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-2 text-xs text-[var(--text-secondary)]">
      <span className="font-medium uppercase tracking-[0.18em] text-[var(--text-muted)]">{t('language.label')}</span>
      <select
        value={locale}
        onChange={e => onChange(e.target.value as Locale)}
        aria-label={t('language.label')}
        className="rounded-full bg-[var(--panel-strong)] px-2 py-1 text-sm text-[var(--text-primary)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--accent)]"
      >
        {LOCALES.map(l => (
          <option key={l.value} value={l.value} className="bg-[var(--panel-strong)] text-[var(--text-primary)]">{l.label}</option>
        ))}
      </select>
    </label>
  )
}
