/** Display rules for this demo, not production alarm thresholds. */
export function freshnessThresholdMs(samplingIntervalMs: number): number {
  return Math.max(60_000, samplingIntervalMs * 3)
}

export function formatAge(ageMs: number): string {
  if (!Number.isFinite(ageMs)) return "时间无效"
  if (ageMs < -5_000) return "时间超前，请检查时钟"
  const seconds = Math.max(0, ageMs / 1000)
  if (seconds < 60) return `${seconds.toFixed(1)} 秒`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`
  return `${Math.floor(seconds / 86400)} 天 ${Math.floor((seconds % 86400) / 3600)} 小时`
}

export function timePosition(time: number, start: number, end: number): number {
  return end === start ? 0.5 : (time - start) / (end - start)
}
