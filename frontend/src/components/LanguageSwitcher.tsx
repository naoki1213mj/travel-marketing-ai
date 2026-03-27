import type { Locale } from '../lib/i18n';

const LOCALES: { value: Locale; label: string }[] = [
  { value: 'ja', label: '日本語' },
  { value: 'en', label: 'English' },
  { value: 'zh', label: '中文' },
]

interface LanguageSwitcherProps {
  locale: Locale
  onChange: (locale: Locale) => void
}

export function LanguageSwitcher({ locale, onChange }: LanguageSwitcherProps) {
  return (
    <select
      value={locale}
      onChange={e => onChange(e.target.value as Locale)}
      className="rounded border border-gray-200 bg-transparent px-2 py-1 text-xs text-gray-600
                 focus:outline-none dark:border-gray-700 dark:text-gray-400"
    >
      {LOCALES.map(l => (
        <option key={l.value} value={l.value}>{l.label}</option>
      ))}
    </select>
  )
}
