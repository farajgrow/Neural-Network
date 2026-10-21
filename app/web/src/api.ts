// Types mirror contract/vocab.json, contract/matcher.schema.json and analysis/README.md.

export const STEPS = [
  'initial_contact', 'platform_switch', 'trust_building', 'authority_claim',
  'urgency_pressure', 'fake_proof_shown', 'small_payment', 'large_payment',
  'credentials_shared', 'remote_access_granted', 'withdrawal_blocked',
  'victim_suspicious', 'third_party_intervened', 'victim_exits', 'money_lost',
] as const
export type Step = (typeof STEPS)[number]

export const SCAM_TYPES = [
  'pig_butchering', 'fake_job', 'impersonation', 'marketplace', 'romance', 'other',
] as const
export type ScamType = (typeof SCAM_TYPES)[number]
/** Graph keys: a scam type or the combined "all" graph. */
export type GraphKey = ScamType | 'all'

export type Outcome = 'lost_money' | 'escaped' | 'unknown'

export interface ScamTypeSummary {
  scam_type: ScamType
  n_stories: number
  loss_rate: number | null
}

export type GraphNode = {
  step: Step
  count: number
  loss_rate: number | null
  avg_position: number
}

export interface GraphEdge {
  from: Step
  to: Step
  count: number
}

export interface PinchPoint {
  step: Step
  loss_rate: number | null
  prev_loss_rate: number | null
  jump: number
}

export interface EscapePoint {
  step: Step
  exit_count: number
  exit_rate: number
}

export interface Graph {
  scam_type: GraphKey
  n_stories: number
  loss_rate: number | null
  outcome_counts: Record<Outcome, number>
  nodes: GraphNode[]
  edges: GraphEdge[]
  pinch_points: PinchPoint[]
  escape_points: EscapePoint[]
  step_to_story_ids: Partial<Record<Step, string[]>>
}

export interface Cluster {
  id: string
  label: string
  size: number
  loss_rate: number | null
  scam_type_counts: Partial<Record<ScamType, number>>
  typical_sequence: Step[]
}

export interface Clusters {
  method: string
  assignments: Record<string, string>
  clusters: Cluster[]
}

export interface StorySummary {
  id: string
  scam_type: ScamType
  outcome: Outcome
  amount_lost_usd: number | null
  snippet: string
  steps: Step[]
}

export interface MatchResult {
  scam_type: ScamType
  confidence: number
  steps_so_far: Step[]
  likely_next_step: Step
  advice: string
  loss_rate_if_continues: number | null
  source: 'live' | 'mock'
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  scamTypes: () => request<ScamTypeSummary[]>('/api/scam-types'),
  graph: (key: GraphKey) => request<Graph>(`/api/graph/${encodeURIComponent(key)}`),
  stories: (params: { scam_type?: GraphKey; step?: Step }) => {
    const q = new URLSearchParams()
    if (params.scam_type) q.set('scam_type', params.scam_type)
    if (params.step) q.set('step', params.step)
    return request<StorySummary[]>(`/api/stories?${q}`)
  },
  clusters: () => request<Clusters | null>('/api/clusters'),
  match: (text: string) =>
    request<MatchResult>('/api/match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }),
}

export const label = (s: string) => s.replace(/_/g, ' ')
export const pct = (x: number | null | undefined) => (x == null ? '—' : `${Math.round(x * 100)}%`)
