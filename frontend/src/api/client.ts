/** 统一请求封装：拼后端地址、抛网络错误、给页脚留一句可读的说明。 */
const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export interface ConflictDetail<T = unknown> {
  detail: string
  current: T
}

/** 409 冲突：服务端在 current 里带回最新事实，调用方必须据此重读后再决定。 */
export class ApiConflictError<T = unknown> extends Error {
  readonly status = 409
  readonly current: T

  constructor(message: string, current: T) {
    super(message)
    this.name = 'ApiConflictError'
    this.current = current
  }
}

export function request(path: string, init?: RequestInit): Promise<Response> {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`
  return fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  }).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '请求未送达'
    throw new Error(`接口请求失败：${detail}`)
  })
}

/** 读接口：非 2xx 统一抛出可读错误。 */
export async function fetchJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}

/** 写接口：200 走业务体；409 抛出带服务端当前快照的冲突错误，其余按 HTTP 错误处理。 */
export async function postJson<T>(path: string, body: unknown, init?: RequestInit): Promise<T> {
  const response = await request(path, { ...init, method: init?.method ?? 'POST', body: JSON.stringify(body) })
  if (response.status === 409) {
    const payload = (await response.json()) as ConflictDetail<T>
    throw new ApiConflictError<T>(payload.detail, payload.current)
  }
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}，数据未更新`)
  }
  return (await response.json()) as T
}

/** 生成一次性幂等键：同一次保存动作的中断重试都带同一个键，服务端只生效一次。 */
export function createRequestKey(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID()
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2)}`
}
