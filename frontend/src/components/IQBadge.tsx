/**
 * 3IQ (Work IQ / Fabric IQ / Foundry IQ) のブランドチップ + 上部ステータスストリップ。
 *
 * デモで「どの IQ が使われたか」を視覚的にアピールするためのコンポーネント:
 * - `IQBadge` — 1 つの IQ ブランドを色付きチップで表示
 * - `IQStatusStrip` — 3 つの IQ ブランドの ON/OFF を一目で見せる top-level header
 */

import { BookOpen, Database, Sparkles, type LucideIcon } from 'lucide-react'
import { type IQBrand, IQ_BRANDS, collectActiveIQBrands } from '../lib/iq-brand'
import type { ToolEvent } from '../lib/tool-events'

const ICONS: Record<IQBrand, LucideIcon> = {
  work_iq: Sparkles,
  fabric_iq: Database,
  foundry_iq: BookOpen,
}

interface IQBadgeProps {
  brand: IQBrand
  t: (key: string) => string
  size?: 'sm' | 'md'
}

/** 1 つの IQ ブランドを色付きチップで表示する。 */
export function IQBadge({ brand, t, size = 'sm' }: IQBadgeProps) {
  const meta = IQ_BRANDS[brand]
  const Icon = ICONS[brand]
  const labelKey = `iq.${brand}.label`
  const translated = t(labelKey)
  const label = translated === labelKey ? meta.defaultLabel : translated
  const padding = size === 'sm' ? 'px-2 py-0.5 text-[10px]' : 'px-2.5 py-1 text-xs'
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border font-semibold tracking-wide ${padding} ${meta.chipClass}`}
      title={t(meta.descriptionKey) === meta.descriptionKey ? meta.defaultLabel : t(meta.descriptionKey)}
    >
      <Icon className={size === 'sm' ? 'h-3 w-3' : 'h-3.5 w-3.5'} aria-hidden />
      <span>{label}</span>
    </span>
  )
}

interface IQStatusStripProps {
  toolEvents: ToolEvent[]
  t: (key: string) => string
}

/**
 * Workflow 上部に常設する "3IQ Status" インジケータ。
 *
 * 各 IQ について:
 * - 使われていれば色付きチップ + 「使用中」badge
 * - まだ使われていなければ淡いグレーアウト + 「待機」
 *
 * 1 つのプロンプトの実行中にどの IQ がいつ点灯したか視覚的に追えるため、
 * デモで「Microsoft 3IQ をフルに使っている」アピールに直結する。
 */
export function IQStatusStrip({ toolEvents, t }: IQStatusStripProps) {
  const activeBrands = collectActiveIQBrands(toolEvents)
  const allBrands: IQBrand[] = ['work_iq', 'fabric_iq', 'foundry_iq']
  return (
    <div className="rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-strong)] p-3" data-testid="iq-status-strip">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-[11px] font-semibold uppercase tracking-[0.16em] text-[var(--text-muted)]">
          {(() => {
            const k = 'iq.status_strip.title'
            const v = t(k)
            return v === k ? 'Microsoft 3IQ usage' : v
          })()}
        </h4>
        <span className="text-[10px] text-[var(--text-muted)]">
          {(() => {
            const k = 'iq.status_strip.active_count'
            const v = t(k)
            const tpl = v === k ? '{n} / 3 used in this run' : v
            return tpl.replace('{n}', String(activeBrands.size))
          })()}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {allBrands.map(brand => {
          const meta = IQ_BRANDS[brand]
          const isActive = activeBrands.has(brand)
          const Icon = ICONS[brand]
          const labelKey = `iq.${brand}.label`
          const labelTranslated = t(labelKey)
          const label = labelTranslated === labelKey ? meta.defaultLabel : labelTranslated
          const descKey = meta.descriptionKey
          const descTranslated = t(descKey)
          const description = descTranslated === descKey ? '' : descTranslated
          return (
            <div
              key={brand}
              data-testid={`iq-status-${brand}`}
              data-iq-active={isActive ? 'true' : 'false'}
              className={`rounded-xl border p-2.5 transition ${
                isActive
                  ? meta.tileClass
                  : 'border-[var(--panel-border)] bg-[var(--surface)] text-[var(--text-secondary)]'
              }`}
            >
              <div className="flex items-center gap-1.5">
                <Icon className="h-3.5 w-3.5" aria-hidden />
                <span className="text-xs font-semibold">{label}</span>
                {isActive ? (
                  <span className="ml-auto inline-flex items-center gap-1">
                    <span className="h-1.5 w-1.5 rounded-full bg-current" />
                    <span className="text-[10px] uppercase tracking-wide">
                      {(() => {
                        const k = 'iq.status.active'
                        const v = t(k)
                        return v === k ? 'Used' : v
                      })()}
                    </span>
                  </span>
                ) : (
                  <span className="ml-auto text-[10px] uppercase tracking-wide">
                    {(() => {
                      const k = 'iq.status.idle'
                      const v = t(k)
                      return v === k ? 'Not yet used' : v
                    })()}
                  </span>
                )}
              </div>
              {description ? (
                <p className={`mt-1 text-[10px] leading-snug ${isActive ? '' : 'text-[var(--text-muted)]'}`}>{description}</p>
              ) : null}
            </div>
          )
        })}
      </div>
      <p className="mt-2 text-[10px] text-[var(--text-muted)]">
        {(() => {
          const k = 'iq.status_strip.legend'
          const v = t(k)
          return v === k ? 'IQ chips only appear for Azure-grounded sources' : v
        })()}
      </p>
    </div>
  )
}
