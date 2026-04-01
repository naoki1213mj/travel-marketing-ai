import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props { children: ReactNode }
interface State { hasError: boolean; error: Error | null }

const FALLBACK_MESSAGES = {
  ja: { title: 'エラーが発生しました', action: '再読み込み' },
  en: { title: 'Something went wrong', action: 'Reload' },
  zh: { title: '发生错误', action: '重新加载' },
} as const

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      const lang = document.documentElement.lang === 'en' || document.documentElement.lang === 'zh'
        ? document.documentElement.lang
        : 'ja'
      const messages = FALLBACK_MESSAGES[lang]

      return (
        <div className="flex min-h-screen items-center justify-center bg-[var(--app-bg)] px-4">
          <div className="max-w-md rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-8 text-center shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
            <h2 className="mb-4 text-xl font-bold text-[var(--text-primary)]">{messages.title}</h2>
            <p className="mb-4 text-sm text-[var(--text-secondary)]">{this.state.error?.message}</p>
            <button
              type="button"
              onClick={() => { this.setState({ hasError: false, error: null }); window.location.reload() }}
              className="rounded-lg bg-[var(--accent)] px-4 py-2 font-medium text-white transition-opacity hover:opacity-90"
            >
              {messages.action}
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
