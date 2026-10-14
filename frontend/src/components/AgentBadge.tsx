import { AGENTS } from '../lib/agents'
import type { AgentId } from '../types'

const GLYPHS: Record<AgentId, string> = {
  boss: 'M12 30 L14 16 L20 22 L25 13 L30 22 L36 16 L38 30 Z',
  inventory: 'M14 14h9v9h-9zM25 14h9v9h-9zM19.5 25h9v9h-9z',
  accounting: 'M24 13v22M30 18c-1-2-3-3-6-3-4 0-6 2-6 4.500s2 4 6 4.500 6 2 6 4.500-2 4.500-6 4.500c-3 0-5-1-6.500-3',
  facilities: 'M14 36V16l10-5 10 5v20zM19 20h3v3h-3zM26 20h3v3h-3zM19 27h3v3h-3zM26 27h3v3h-3z',
  customer_service: 'M17 24h.01M24 24h.01M31 24h.01',
}

function Shape({ id }: { id: AgentId }) {
  const fill = AGENTS[id].color
  switch (AGENTS[id].shape) {
    case 'hexagon':
      return <polygon points="24,3 43,13.500 43,34.500 24,45 5,34.500 5,13.500" fill={fill} />
    case 'square':
      return <rect x="5" y="5" width="38" height="38" rx="6" fill={fill} />
    case 'diamond':
      return <rect x="9" y="9" width="30" height="30" rx="4" fill={fill} transform="rotate(45 24 24)" />
    case 'arch':
      return <path d="M6 44V22C6 11 14 4 24 4s18 7 18 18v22z" fill={fill} />
    case 'bubble':
      return <path d="M8 6h32a4 4 0 0 1 4 4v22a4 4 0 0 1-4 4H22l-9 8v-8H8a4 4 0 0 1-4-4V10a4 4 0 0 1 4-4z" fill={fill} />
  }
}

/** Each agent has its own silhouette AND glyph as well as colour, so identity never depends on colour alone. */
export function AgentBadge({ id, size = 44 }: { id: AgentId; size?: number }) {
  const meta = AGENTS[id]
  return (
    <svg className="agent-badge" width={size} height={size} viewBox="0 0 48 48" role="img" aria-label={`${meta.name} badge`}>
      <Shape id={id} />
      <path
        d={GLYPHS[id]}
        fill={id === 'accounting' || id === 'customer_service' ? 'none' : meta.ink}
        stroke={meta.ink}
        strokeWidth={id === 'accounting' ? 2.500 : id === 'customer_service' ? 5 : 0}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={id === 'inventory' || id === 'facilities' ? 0.95 : 1}
      />
    </svg>
  )
}
