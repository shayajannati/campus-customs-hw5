export function money(value: number): string {
  return value.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

export function clock(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** "in 2 days" / "3 days overdue" from a signed day count (negative = past due). */
export function dueText(days: number | null): string {
  if (days === null) return ''
  if (days === 0) return 'due today'
  return days > 0 ? `due in ${days} day${days === 1 ? '' : 's'}` : `${-days} day${days === -1 ? '' : 's'} overdue`
}

export function titleCase(value: string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}
