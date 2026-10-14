import type { AgentId } from '../types'

export interface AgentMeta {
  id: AgentId
  name: string
  role: string
  /** Badge fill. Shape and icon also differ per agent, so colour is never the only cue. */
  color: string
  ink: string
  shape: 'hexagon' | 'square' | 'diamond' | 'arch' | 'bubble'
}

export const AGENT_ORDER: AgentId[] = ['boss', 'inventory', 'accounting', 'facilities', 'customer_service']

export const AGENTS: Record<AgentId, AgentMeta> = {
  boss: { id: 'boss', name: 'Boss', role: 'Reads the ticket, delegates, recommends', color: '#0b2a55', ink: '#ffffff', shape: 'hexagon' },
  inventory: { id: 'inventory', name: 'Inventory', role: 'Stock, sizes, shortfalls', color: '#1e6bb8', ink: '#ffffff', shape: 'square' },
  accounting: { id: 'accounting', name: 'Accounting', role: 'Invoices, cash, payment records', color: '#3a4aa8', ink: '#ffffff', shape: 'diamond' },
  facilities: { id: 'facilities', name: 'Facilities', role: 'Lease and rent checks', color: '#1b7f9a', ink: '#ffffff', shape: 'arch' },
  customer_service: { id: 'customer_service', name: 'Customer Service', role: 'Drafts customer-safe replies', color: '#9cc3ec', ink: '#0b2a55', shape: 'bubble' },
}

export function isAgentId(value: string): value is AgentId {
  return value in AGENTS
}

export function agentName(value: string): string {
  return isAgentId(value) ? AGENTS[value].name : value
}
