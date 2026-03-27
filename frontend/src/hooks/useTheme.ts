/**
 * テーマ管理フック。ダーク/ライト/システム連動をサポートする。
 */

import { useCallback, useEffect, useState } from 'react'

export type Theme = 'light' | 'dark' | 'system'

function getSystemTheme(): 'light' | 'dark' {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyTheme(theme: Theme) {
  const resolved = theme === 'system' ? getSystemTheme() : theme
  document.documentElement.classList.toggle('dark', resolved === 'dark')
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(() => {
    const saved = localStorage.getItem('theme') as Theme | null
    return saved || 'system'
  })

  const setTheme = useCallback((newTheme: Theme) => {
    setThemeState(newTheme)
    localStorage.setItem('theme', newTheme)
    applyTheme(newTheme)
  }, [])

  useEffect(() => {
    applyTheme(theme)
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = () => { if (theme === 'system') applyTheme('system') }
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [theme])

  return { theme, setTheme }
}
