// Colour scales shared by the graph, legend and side panel.

const STOPS: [number, [number, number, number]][] = [
  [0, [52, 211, 153]], // green
  [0.5, [245, 176, 74]], // amber
  [1, [242, 92, 102]], // red
]

export const NEUTRAL = '#6b7686'
export const LOST = '#f25c66'
export const SAFE = '#34d399'
export const MATCH = '#8ea2ff'

/** Loss rate (0..1) on a green → amber → red scale; null is neutral grey. */
export function lossColor(rate: number | null | undefined): string {
  if (rate == null) return NEUTRAL
  const r = Math.min(1, Math.max(0, rate))
  for (let i = 1; i < STOPS.length; i++) {
    const [p1, c1] = STOPS[i]
    const [p0, c0] = STOPS[i - 1]
    if (r <= p1) {
      const t = (r - p0) / (p1 - p0)
      const c = c0.map((v, j) => Math.round(v + (c1[j] - v) * t))
      return `rgb(${c[0]}, ${c[1]}, ${c[2]})`
    }
  }
  return LOST
}

export const LOSS_GRADIENT = `linear-gradient(90deg, ${lossColor(0)}, ${lossColor(0.5)}, ${lossColor(1)})`

const CLUSTER_COLORS = ['#b197fc', '#3bc9db', '#f783ac', '#a9e34b', '#ffa94d', '#74c0fc', '#e599f7', '#63e6be']

export function clusterColor(index: number, outlier?: boolean): string {
  return outlier ? '#adb5bd' : CLUSTER_COLORS[index % CLUSTER_COLORS.length]
}
