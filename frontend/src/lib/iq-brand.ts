/**
 * 3IQ ブランド分類: SSE evidence / tool events を Work IQ / Fabric IQ / Foundry IQ に分類する。
 *
 * バックエンド (`src/agents/*.py` の `_emit_evidence_event` / `emit_tool_event`) が emit する
 * `source` / `tool` / `agent` の値から、ユーザーに見せる「どの IQ が使われたか」を導出する。
 * ブランドごとに色 + アイコン名を返し、`IQBadge` / `IQStatusStrip` から共通利用する。
 *
 * デザイン原則:
 * - **azure-grounded のときだけ IQ ブランドを表示する**。`source=local` / `local-check` / `fallback` 等は
 *   IQ ブランドを返さない (silent fallback の signal を消さないため)。
 * - tool 名 / agent 名 / source / metadata.runtime を順に見て分類する。
 * - 不明な source は `null` を返す (= IQ チップは出さず、既存の uppercase ラベルを使う)。
 */

import type { EvidenceItem } from './event-schemas'
import type { ToolEvent } from './tool-events'

export type IQBrand = 'work_iq' | 'fabric_iq' | 'foundry_iq'

export interface IQBrandMeta {
  brand: IQBrand
  /** UI で表示するブランド名 (i18n 経由で上書き可) */
  defaultLabel: string
  /** Tailwind class — 小さい inline chip 用 (EvidencePanel カードなど) */
  chipClass: string
  /** Tailwind class — IQStatusStrip の大きいタイル用 (より強いコントラスト) */
  tileClass: string
  /** 凡例で使う 1 行の説明 (i18n key, fallback あり) */
  descriptionKey: string
  /** lucide-react のアイコン名 (component import 側で resolve) */
  iconName: 'Sparkles' | 'Database' | 'BookOpen'
}

export const IQ_BRANDS: Record<IQBrand, IQBrandMeta> = {
  work_iq: {
    brand: 'work_iq',
    defaultLabel: 'Work IQ',
    chipClass:
      'border-sky-300/70 bg-sky-100/80 text-sky-800 dark:border-sky-700/60 dark:bg-sky-950/40 dark:text-sky-200',
    tileClass:
      'border-sky-400 bg-sky-100 text-sky-900 dark:border-sky-700/60 dark:bg-sky-950/40 dark:text-sky-100',
    descriptionKey: 'iq.work_iq.description',
    iconName: 'Sparkles',
  },
  fabric_iq: {
    brand: 'fabric_iq',
    defaultLabel: 'Fabric IQ',
    chipClass:
      'border-emerald-300/70 bg-emerald-100/80 text-emerald-800 dark:border-emerald-700/60 dark:bg-emerald-950/40 dark:text-emerald-200',
    tileClass:
      'border-emerald-400 bg-emerald-100 text-emerald-900 dark:border-emerald-700/60 dark:bg-emerald-950/40 dark:text-emerald-100',
    descriptionKey: 'iq.fabric_iq.description',
    iconName: 'Database',
  },
  foundry_iq: {
    brand: 'foundry_iq',
    defaultLabel: 'Foundry IQ',
    chipClass:
      'border-violet-300/70 bg-violet-100/80 text-violet-800 dark:border-violet-700/60 dark:bg-violet-950/40 dark:text-violet-200',
    tileClass:
      'border-violet-400 bg-violet-100 text-violet-900 dark:border-violet-700/60 dark:bg-violet-950/40 dark:text-violet-100',
    descriptionKey: 'iq.foundry_iq.description',
    iconName: 'BookOpen',
  },
}

const FABRIC_IQ_SOURCES = new Set([
  'fabric',
  'fabric_lakehouse',
  'fabric_data_agent',
  'fabric_sql',
])

const FOUNDRY_IQ_SOURCES = new Set([
  'foundry',
  'foundry_iq',
  'azure_ai_search',
])

const WORK_IQ_SOURCES = new Set([
  'workiq',
  'work_iq',
])

const FABRIC_IQ_TOOLS = new Set([
  'query_data_agent',
])

const FOUNDRY_IQ_TOOLS = new Set([
  'search_knowledge_base',
  'foundry_iq_search',
])

const WORK_IQ_TOOLS = new Set([
  'workiq_foundry_tool',
  'workiq_graph_prefetch',
  'foundry_prompt_agent',
  'generate_workplace_context_brief',
])

/** evidence item から IQ brand を推定する。azure-grounded でないなら null。 */
export function classifyEvidence(item: EvidenceItem): IQBrand | null {
  const source = (item.source || '').trim().toLowerCase()
  if (FABRIC_IQ_SOURCES.has(source)) return 'fabric_iq'
  if (FOUNDRY_IQ_SOURCES.has(source)) return 'foundry_iq'
  if (WORK_IQ_SOURCES.has(source)) return 'work_iq'
  // local / local-check / fallback はあえて IQ ブランドを付けない (silent fallback の信号を消さない)
  return null
}

/** tool event 全体から IQ brand を推定する。tool 名と agent 名で fallback 判定する。 */
export function classifyToolEvent(event: ToolEvent): IQBrand | null {
  const tool = (event.tool || '').trim().toLowerCase()
  const agent = (event.agent || '').trim().toLowerCase()

  if (WORK_IQ_TOOLS.has(tool)) return 'work_iq'
  if (FABRIC_IQ_TOOLS.has(tool)) {
    // 失敗 / fallback 状態のときはブランドを付けない
    if (event.status === 'failed' || event.fallback) return null
    return 'fabric_iq'
  }
  if (FOUNDRY_IQ_TOOLS.has(tool)) {
    if (event.status === 'failed' || event.fallback) return null
    return 'foundry_iq'
  }

  // search_sales_history / search_customer_reviews は source ベースで分類する
  // (Fabric SQL から取れたら Fabric IQ、CSV fallback なら null)
  if (tool === 'search_sales_history' || tool === 'search_customer_reviews') {
    const evidenceBrands = (event.evidence ?? [])
      .map(item => classifyEvidence(item))
      .filter((b): b is IQBrand => b !== null)
    if (evidenceBrands.includes('fabric_iq')) return 'fabric_iq'
    return null
  }

  // search_safety_info / search_market_trends は上の FOUNDRY_IQ_TOOLS で扱うので二重にしない

  // marketing-plan-agent + work_iq 関連 evidence
  if (agent === 'marketing-plan-agent') {
    const hasWorkIqEvidence = (event.evidence ?? []).some(
      item => (item.source || '').toLowerCase().includes('workiq') ||
              (item.source || '').toLowerCase().includes('m365_copilot') ||
              (item.source || '').toLowerCase().includes('graph')
    )
    if (hasWorkIqEvidence) return 'work_iq'
  }

  return null
}

/** 配列から使われた IQ ブランド集合を返す (重複排除)。failed/fallback の event は無視する。 */
export function collectActiveIQBrands(events: ToolEvent[]): Set<IQBrand> {
  const brands = new Set<IQBrand>()
  for (const event of events) {
    const isFailedOrFallback = event.status === 'failed' || Boolean(event.fallback)
    const fromTool = classifyToolEvent(event)
    if (fromTool) brands.add(fromTool)
    if (isFailedOrFallback) continue
    for (const item of event.evidence ?? []) {
      const fromEvidence = classifyEvidence(item)
      if (fromEvidence) brands.add(fromEvidence)
    }
  }
  return brands
}

/**
 * 指定 IQ ブランドの tool が attempted されたか (成功・失敗・fallback 問わず) を返す。
 *
 * Bug 3 (per-step IQ phase header chip) で使う。`collectActiveIQBrands` は
 * fallback / failed のときに null を返してチップを消すが、それだと
 * "Fabric DA を呼んだが SQL に fallback した" 状況で Fabric IQ chip が
 * 一切出ないため、デモで provider 設計意図が伝わらない。
 *
 * このヘルパーは tool 名が IQ family に属するかだけを見るので、
 * 「呼ぼうとしたが失敗した」のも attempted として扱う。
 */
export function hasIQAttempted(events: ToolEvent[], brand: IQBrand): boolean {
  for (const event of events) {
    const tool = (event.tool || '').trim().toLowerCase()
    if (brand === 'fabric_iq') {
      if (FABRIC_IQ_TOOLS.has(tool)) return true
      if (tool === 'search_sales_history' || tool === 'search_customer_reviews') return true
    } else if (brand === 'foundry_iq') {
      if (FOUNDRY_IQ_TOOLS.has(tool)) return true
    } else if (brand === 'work_iq') {
      if (WORK_IQ_TOOLS.has(tool)) return true
    }
  }
  return false
}
