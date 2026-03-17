import type { ExecutionStatus } from './types'

export function statusClass(status: ExecutionStatus): string {
  if (status === "running" || status === "starting") return "badge-active"
  if (status === "completed") return "badge-completed"
  if (status === "failed" || status === "stopped") return "badge-error"
  return ""
}

export function timeAgo(dateStr: string | null): string {
  if (!dateStr) return ""
  const d = new Date(dateStr)
  const now = new Date()
  const diff = Math.floor((now.getTime() - d.getTime()) / 1000)
  if (diff < 60) return "just now"
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}
