import { AlertTriangle, BookOpen, Check, Database, Globe, Image, Loader2, Scale, Search, Star, Target, Wrench } from 'lucide-react'
import type { ToolEvent } from '../hooks/useSSE'

const TOOL_ICONS: Record<string, React.ReactNode> = {
  search_sales_history: <Database className="h-3.5 w-3.5" />,
  search_customer_reviews: <Star className="h-3.5 w-3.5" />,
  web_search: <Globe className="h-3.5 w-3.5" />,
  foundry_iq_search: <BookOpen className="h-3.5 w-3.5" />,
  check_ng_expressions: <Search className="h-3.5 w-3.5" />,
  check_travel_law_compliance: <Scale className="h-3.5 w-3.5" />,
  generate_hero_image: <Image className="h-3.5 w-3.5" />,
  generate_banner_image: <Target className="h-3.5 w-3.5" />,
}

interface ToolEventBadgesProps {
  events: ToolEvent[]
  t: (key: string) => string
}

export function ToolEventBadges({ events, t }: ToolEventBadgesProps) {
  if (events.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2 py-2">
      {events.map((event, i) => {
        const source = event.source || (event.agent === 'improvement-mcp' ? 'mcp' : undefined)
        const isCompleted = event.status === 'completed'
        const isFailed = event.status === 'failed'
        const provider = source === 'mcp' ? 'azure-functions-mcp' : 'local'

        return (
          <span
            key={`${event.tool}-${i}`}
            data-tool-name={event.tool}
            data-tool-source={source || 'local'}
            data-tool-provider={provider}
            data-tool-status={event.status}
            className="inline-flex items-center gap-2 rounded-full border border-[var(--panel-border)] bg-[var(--panel-strong)] px-3 py-1.5 text-xs text-[var(--text-secondary)]"
          >
            <span>{TOOL_ICONS[event.tool] || <Wrench className="h-3.5 w-3.5" />}</span>
            <span>{t(`tool.${event.tool}`)}</span>
            {source === 'mcp' && (
              <span className="rounded-full bg-[var(--accent-soft)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--accent-strong)]">
                {t('tool.source.mcp')}
              </span>
            )}
            {event.fallback && (
              <span className="rounded-full border border-amber-300/80 bg-amber-100/80 px-2 py-0.5 text-[10px] font-medium text-amber-800 dark:border-amber-700/70 dark:bg-amber-950/30 dark:text-amber-200">
                {t(`tool.fallback.${event.fallback}`)}
              </span>
            )}
            <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)]">{event.agent}</span>
            <span className={isCompleted ? 'text-green-500' : isFailed ? 'text-amber-500' : 'text-yellow-500'}>
              {isCompleted ? (
                <Check className="h-3 w-3" />
              ) : isFailed ? (
                <AlertTriangle className="h-3 w-3" />
              ) : (
                <Loader2 className="h-3 w-3 animate-spin" />
              )}
            </span>
          </span>
        )
      })}
    </div>
  )
}
