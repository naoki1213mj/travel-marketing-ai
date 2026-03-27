import type { Theme } from '../hooks/useTheme';

interface ThemeToggleProps {
  theme: Theme
  onChange: (theme: Theme) => void
}

const THEMES: { value: Theme; icon: string }[] = [
  { value: 'light', icon: '☀️' },
  { value: 'dark', icon: '🌙' },
  { value: 'system', icon: '💻' },
]

export function ThemeToggle({ theme, onChange }: ThemeToggleProps) {
  return (
    <div className="flex rounded-lg border border-gray-200 dark:border-gray-700">
      {THEMES.map(t => (
        <button
          key={t.value}
          onClick={() => onChange(t.value)}
          className={`px-2 py-1 text-xs transition-colors
            ${theme === t.value
              ? 'bg-gray-100 text-gray-900 dark:bg-gray-700 dark:text-gray-100'
              : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'
            }
            ${t.value === 'light' ? 'rounded-l-lg' : ''}
            ${t.value === 'system' ? 'rounded-r-lg' : ''}`}
          title={t.value}
        >
          {t.icon}
        </button>
      ))}
    </div>
  )
}
