export type CapabilityKey =
  | 'model_router'
  | 'gpt_55'
  | 'foundry_tracing'
  | 'continuous_monitoring'
  | 'cost_metrics'
  | 'mcp_registry'
  | 'source_ingestion'
  | 'voice_live'
  | 'voice_talk_to_start'
  | 'mai_transcribe_1'
  | 'work_iq'

export interface CapabilityFeature {
  available: boolean
  configured: boolean
}

export interface CapabilitySnapshot {
  version: number
  features: Partial<Record<CapabilityKey, CapabilityFeature>>
}

function isCapabilityFeature(value: unknown): value is CapabilityFeature {
  if (!value || typeof value !== 'object') return false
  const feature = value as Record<string, unknown>
  return typeof feature.available === 'boolean'
    && typeof feature.configured === 'boolean'
}

export function normalizeCapabilities(value: unknown): CapabilitySnapshot | null {
  if (!value || typeof value !== 'object') return null
  const raw = value as Record<string, unknown>
  const rawFeatures = raw.features
  if (!rawFeatures || typeof rawFeatures !== 'object') return null

  const features: Partial<Record<CapabilityKey, CapabilityFeature>> = {}
  for (const [key, feature] of Object.entries(rawFeatures as Record<string, unknown>)) {
    if (isCapabilityFeature(feature)) {
      features[key as CapabilityKey] = feature
    }
  }

  return {
    version: typeof raw.version === 'number' ? raw.version : 1,
    features,
  }
}

export function isCapabilityAvailable(
  snapshot: CapabilitySnapshot | null,
  key: CapabilityKey,
): boolean | null {
  return snapshot?.features[key]?.available ?? null
}

export async function fetchCapabilities(): Promise<CapabilitySnapshot | null> {
  try {
    const response = await fetch('/api/capabilities')
    if (!response.ok) return null
    return normalizeCapabilities(await response.json())
  } catch {
    return null
  }
}
