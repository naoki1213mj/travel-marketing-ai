import { AlertTriangle, BookOpen, Building2, Check, Database, FileSearch, Globe, Image, Loader2, Scale, Search, Sparkles, Star, Target, Video, Wand2, Wrench } from 'lucide-react'
import type { ToolEvent } from '../hooks/useSSE'
import { collapseToolEvents, isFoundryWorkIqToolEvent, isToolAttentionStatus, resolveToolProvider } from '../lib/tool-events'

const TOOL_ICONS: Record<string, React.ReactNode> = {
  query_data_agent: <Database className="h-3.5 w-3.5" />,
  search_sales_history: <Database className="h-3.5 w-3.5" />,
  search_customer_reviews: <Star className="h-3.5 w-3.5" />,
  web_search: <Globe className="h-3.5 w-3.5" />,
  foundry_iq_search: <BookOpen className="h-3.5 w-3.5" />,
  code_interpreter: <Sparkles className="h-3.5 w-3.5" />,
  check_ng_expressions: <Search className="h-3.5 w-3.5" />,
  check_travel_law_compliance: <Scale className="h-3.5 w-3.5" />,
  generate_hero_image: <Image className="h-3.5 w-3.5" />,
  generate_banner_image: <Target className="h-3.5 w-3.5" />,
  analyze_existing_brochure: <FileSearch className="h-3.5 w-3.5" />,
  generate_improvement_brief: <Wand2 className="h-3.5 w-3.5" />,
  generate_promo_video: <Video className="h-3.5 w-3.5" />,
  review_plan_quality: <Search className="h-3.5 w-3.5" />,
  review_brochure_accessibility: <Search className="h-3.5 w-3.5" />,
}

function resolveToolLabel(tool: string, t: (key: string) => string): string {
  const translationKey = `tool.${tool}`
  const translated = t(translationKey)
  if (translated !== translationKey) {
    return translated
  }

  return tool.replaceAll('_', ' ')
}

function resolveToolIcon(tool: string, source: string | undefined): React.ReactNode {
  if (TOOL_ICONS[tool]) {
    return TOOL_ICONS[tool]
  }

  if (source === 'workiq') {
    return <Building2 className="h-3.5 w-3.5" />
  }

  return <Wrench className="h-3.5 w-3.5" />
}

interface ToolEventBadgesProps {
  events: ToolEvent[]
  t: (key: string) => string
}

export function ToolEventBadges({ events, t }: ToolEventBadgesProps) {
  const collapsedEvents = collapseToolEvents(events)
  if (collapsedEvents.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2 py-2">
      {collapsedEvents.map((event, i) => {
        const source = event.source || (event.agent === 'improvement-mcp' ? 'mcp' : undefined)
        const provider = resolveToolProvider(event)
        const isFoundryWorkIq = isFoundryWorkIqToolEvent(event)
        const isCompleted = event.status === 'completed' || event.status === 'ok'
        const isFailed = isToolAttentionStatus(event.status)
        const providerToken = provider === 'mcp'
          ? 'azure-functions-mcp'
          : provider
        const statusLabelKey = isToolAttentionStatus(event.status) ? `tool.status.${event.status}` : null

        return (
          <span
            key={`${event.tool}-${i}`}
            data-tool-name={event.tool}
            data-tool-source={source || 'local'}
            data-tool-provider={providerToken}
            data-tool-status={event.status}
            className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs ${
              isFoundryWorkIq
                ? 'border-violet-300/70 bg-violet-50/80 text-violet-900 dark:border-violet-700/60 dark:bg-violet-950/30 dark:text-violet-100'
                : 'border-[var(--panel-border)] bg-[var(--panel-strong)] text-[var(--text-secondary)]'
            }`}
          >
            <span>{resolveToolIcon(event.tool, source)}</span>
            <span>{event.display_name || resolveToolLabel(event.tool, t)}</span>
            {isFoundryWorkIq ? (
              <span
                data-tool-kind="foundry-workiq"
                className="rounded-full border border-violet-300/70 bg-violet-100/80 px-2 py-0.5 text-[10px] font-semibold text-violet-800 dark:border-violet-700/60 dark:bg-violet-950/40 dark:text-violet-200"
              >
                {t('tool.source.foundry')} {t('tool.source.workiq')}
              </span>
            ) : (
              <>
                {provider === 'mcp' && (
                  <span className="rounded-full bg-[var(--accent-soft)] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--accent-strong)]">
                    {t('tool.source.mcp')}
                  </span>
                )}
                {provider === 'workiq' && (
                  <span className="rounded-full border border-violet-300/70 bg-violet-100/80 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-violet-800 dark:border-violet-700/60 dark:bg-violet-950/40 dark:text-violet-200">
                    {t('tool.source.workiq')}
                  </span>
                )}
                {provider === 'foundry' && (
                  <span className="rounded-full border border-sky-300/70 bg-sky-100/80 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-sky-800 dark:border-sky-700/60 dark:bg-sky-950/40 dark:text-sky-200">
                    {t('tool.source.foundry')}
                  </span>
                )}
              </>
            )}
            {event.source_scope?.map((scope) => (
              <span
                key={`${event.tool}-${i}-${scope}`}
                className="rounded-full border border-[var(--panel-border)] bg-[var(--surface)] px-2 py-0.5 text-[10px] font-medium text-[var(--text-muted)]"
              >
                {t(`settings.workiq.source.${scope}`)}
              </span>
            ))}
            {statusLabelKey && (
              <span className="rounded-full border border-amber-300/80 bg-amber-100/80 px-2 py-0.5 text-[10px] font-medium text-amber-800 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-200">
                {t(statusLabelKey)}
              </span>
            )}
            {event.fallback && (
              <span className="rounded-full border border-amber-300/80 bg-amber-100/80 px-2 py-0.5 text-[10px] font-medium text-amber-800 dark:border-amber-700/70 dark:bg-amber-950/30 dark:text-amber-200">
                {t(`tool.fallback.${event.fallback}`)}
              </span>
            )}
            {event.inferred && (
              <span className="rounded-full border border-[var(--panel-border)] bg-[var(--surface)] px-2 py-0.5 text-[10px] font-medium text-[var(--text-muted)]">
                {t('tool.meta.inferred')}
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
