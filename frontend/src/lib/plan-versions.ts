/** 企画書ドラフトのバージョンリストを構築する */
export function buildPlanVersions(
  textContents: Array<{ agent?: string; content?: string }>,
): Array<{ label: string; content: string }> {
  const plans = textContents.filter(c => c.agent === 'marketing-plan-agent' && c.content)
  if (plans.length <= 1) return []

  return plans.map((p, i) => ({
    label: `v${i + 1}`,
    content: p.content || '',
  }))
}
