/**
 * 多言語フック。日/英/中の切替をサポートする。
 */

import { useCallback, useState } from 'react'
import { translations, type Locale } from '../lib/i18n'

export function useI18n() {
  const [locale, setLocaleState] = useState<Locale>(() => {
    const saved = localStorage.getItem('locale') as Locale | null
    return saved || 'ja'
  })

  const setLocale = useCallback((newLocale: Locale) => {
    setLocaleState(newLocale)
    localStorage.setItem('locale', newLocale)
  }, [])

  const t = useCallback((key: string): string => {
    return translations[locale][key] || key
  }, [locale])

  return { locale, setLocale, t }
}
