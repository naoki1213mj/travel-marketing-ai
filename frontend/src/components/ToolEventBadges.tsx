import type { ToolEvent } from '../hooks/useSSE'

const TOOL_ICONS: Record<string, string> = {
  search_sales_history: '📁',
  search_customer_reviews: '⭐',
  web_search: '🌐',
  foundry_iq_search: '📚',
  check_ng_expressions: '🔍',
  check_travel_law_compliance: '⚖️',
  generate_hero_image: '🖼️',
  generate_banner_image: '🎯',
}

interface ToolEventBadgesProps {
  events: ToolEvent[]
}

export function ToolEventBadges({ events }: ToolEventBadgesProps) {
  if (events.length === 0) return null

  return (
    <div className="flex flex-wrap gap-2 py-2">
      {events.map((event, i) => (
        <span
          key={`${event.tool}-${i}`}
          className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-3 py-1
                     text-xs text-gray-700
                     dark:bg-gray-800 dark:text-gray-300"
        >
          <span>{TOOL_ICONS[event.tool] || '🔧'}</span>
          <span>{event.tool}</span>
          <span className={event.status === 'completed' ? 'text-green-500' : 'text-yellow-500'}>
            {event.status === 'completed' ? '✓' : '⏳'}
          </span>
        </span>
      ))}
    </div>
  )
}
