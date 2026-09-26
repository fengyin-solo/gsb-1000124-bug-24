import { defineStore } from 'pinia'

import { ApiConflictError, createRequestKey, fetchJson, postJson } from '@/api/client'

export interface ResultEntry {
  id: number
  version: number
  结果编号: string | null
  所属任务: string | null
  检测项: string | null
  实测值: string | null
  标准限值: string | null
  判定结论: string | null
  检测日期: string | null
  status: string
  结果状态: string
  pending: boolean
  abnormal: boolean
}

export interface ResultPage {
  items: ResultEntry[]
  total: number
  page: number
  size: number
}

export interface WriteResult {
  ok: boolean
  message: string
  entry: ResultEntry | null
}

export { ApiConflictError }

/**
 * 检测结果的前端唯一事实源：
 * - listCache / detailCache 都只允许被"服务端返回的快照"整体替换，
 *   所以保存成功后两份缓存立刻一致，重新进入页面读到的也是这份；
 * - 所有写操作带 requestKey（幂等）+ version（乐观锁），
 *   409 时先用响应里的 current 收敛缓存，再把冲突交给页面提示用户；
 * - inFlight 拦截同一记录并发的重复点击；requestKey 落在 sessionStorage，
 *   异常中断后重试仍复用同一键，服务端只生效一次，不留半成品。
 */
const FLIGHT_PREFIX = 'result:flight:'

function rememberFlight(token: string, key: string, value: string) {
  try {
    sessionStorage.setItem(FLIGHT_PREFIX + token, JSON.stringify({ key, value }))
  } catch {
    /* 隐私模式等场景下降级为仅内存去重 */
  }
}

function takeFlight(key: string): string | undefined {
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i -= 1) {
      const storageKey = sessionStorage.key(i)
      if (!storageKey?.startsWith(FLIGHT_PREFIX)) continue
      const raw = sessionStorage.getItem(storageKey)
      if (!raw) continue
      const parsed = JSON.parse(raw) as { key: string; value: string }
      if (parsed.key === key) {
        sessionStorage.removeItem(storageKey)
        return parsed.value
      }
    }
  } catch {
    /* 忽略不可用的存储 */
  }
  return undefined
}

function clearFlightByValue(requestKey: string) {
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i -= 1) {
      const storageKey = sessionStorage.key(i)
      if (!storageKey?.startsWith(FLIGHT_PREFIX)) continue
      const raw = sessionStorage.getItem(storageKey)
      if (raw && (JSON.parse(raw) as { value?: string }).value === requestKey) {
        sessionStorage.removeItem(storageKey)
      }
    }
  } catch {
    /* 忽略不可用的存储 */
  }
}

export const useResultStore = defineStore('result', {
  state: () => ({
    listCache: [] as ResultEntry[],
    listTotal: 0,
    detailCache: {} as Record<number, ResultEntry>,
    loadingList: false,
    inFlight: {} as Record<string, boolean>,
    listSeq: 0,
    detailSeq: {} as Record<number, number>,
  }),
  getters: {
    pendingCount: (state) => state.listCache.filter((row) => row.pending).length,
    statusCount: (state) => (status: string) =>
      state.listCache.filter((row) => row.status === status).length,
  },
  actions: {
    /** 用服务端快照同时收敛列表与详情两份缓存——它们永远不会分叉。 */
    adopt(entry: ResultEntry | null) {
      if (!entry) return
      this.detailCache[entry.id] = entry
      const index = this.listCache.findIndex((row) => row.id === entry.id)
      if (index >= 0) {
        this.listCache.splice(index, 1, entry)
      } else {
        this.listCache.unshift(entry)
        this.listTotal += 1
      }
    },

    /** 冲突或重进页面时，以服务端内容无条件覆盖本地两份缓存。 */
    adoptFromServer(entry: ResultEntry) {
      this.detailCache[entry.id] = entry
      const index = this.listCache.findIndex((row) => row.id === entry.id)
      if (index >= 0) this.listCache.splice(index, 1, entry)
    },

    /**
     * 取一次写操作的幂等键：同一业务意图（flightKey）在中断重试时复用旧键，
     * 让服务端识别为重复提交；拿到成功/业务失败结论后作废，换新意图才发新键。
     */
    resolveRequestKey(flightKey: string) {
      const reused = takeFlight(flightKey)
      if (reused) return reused
      const requestKey = createRequestKey()
      rememberFlight(`t${Date.now()}-${Math.random().toString(36).slice(2)}`, flightKey, requestKey)
      return requestKey
    },

    async fetchList(params: Record<string, string | number | undefined> = {}) {
      this.loadingList = true
      // 请求序号：先发后到的旧响应不得覆盖更新的列表（保存/筛选竞态）。
      const seq = ++this.listSeq
      try {
        const search = new URLSearchParams()
        for (const [key, value] of Object.entries(params)) {
          if (value !== undefined && value !== '') search.set(key, String(value))
        }
        const page = await fetchJson<ResultPage>(`/api/result?${search.toString()}`)
        if (seq !== this.listSeq) return page // 已有更新的请求在途，本次结果作废
        this.listCache = page.items
        this.listTotal = page.total
        for (const item of page.items) this.detailCache[item.id] = item
        return page
      } finally {
        if (seq === this.listSeq) this.loadingList = false
      }
    },

    /** 重新进入详情：永远以服务端为准重读，不信任可能陈旧的本地编辑态。 */
    async fetchDetail(id: number) {
      const seq = (this.detailSeq[id] ?? 0) + 1
      this.detailSeq[id] = seq
      const entry = await fetchJson<ResultEntry>(`/api/result/${id}`)
      if (seq !== this.detailSeq[id]) return entry // 被更新的一次读取顶替
      this.detailCache[id] = entry
      const index = this.listCache.findIndex((row) => row.id === id)
      if (index >= 0) this.listCache.splice(index, 1, entry)
      return entry
    },

    async create(values: Record<string, unknown>) {
      if (this.inFlight.create) {
        return { ok: false, message: '正在提交登记，请勿重复点击', entry: null }
      }
      this.inFlight.create = true
      const requestKey = this.resolveRequestKey('create')
      try {
        const result = await postJson<WriteResult>('/api/result', {
          values,
          request_key: requestKey,
        })
        if (result.ok) {
          clearFlightByValue(requestKey)
          this.adopt(result.entry)
        } else {
          // 业务校验失败说明服务端确定未生效，键可以释放；网络错误则保留供重试回放。
          clearFlightByValue(requestKey)
        }
        return result
      } finally {
        this.inFlight.create = false
      }
    },

    /** 保存录入内容。expectedVersion 必须来自最近一次服务端读取。 */
    async save(
      id: number,
      values: Record<string, unknown>,
      expectedVersion: number,
    ): Promise<WriteResult> {
      const flightKey = `save:${id}:${expectedVersion}`
      if (this.inFlight[flightKey]) {
        return { ok: false, message: '正在保存中，请勿重复提交', entry: null }
      }
      this.inFlight[flightKey] = true
      const requestKey = this.resolveRequestKey(flightKey)
      try {
        const result = await postJson<WriteResult>(`/api/result/${id}`, {
          values,
          expected_version: expectedVersion,
          request_key: requestKey,
        }, { method: 'PUT' })
        if (result.ok) {
          clearFlightByValue(requestKey)
          this.adopt(result.entry)
        } else {
          clearFlightByValue(requestKey)
        }
        return result
      } catch (error) {
        if (error instanceof ApiConflictError) {
          clearFlightByValue(requestKey)
          this.adoptFromServer(error.current as ResultEntry)
        }
        throw error
      } finally {
        this.inFlight[flightKey] = false
      }
    },

    async runAction(id: number, action: string, expectedVersion: number): Promise<WriteResult> {
      const flightKey = `action:${id}:${action}:${expectedVersion}`
      if (this.inFlight[flightKey]) {
        return { ok: false, message: '该动作正在处理，请勿重复点击', entry: null }
      }
      this.inFlight[flightKey] = true
      const requestKey = this.resolveRequestKey(flightKey)
      try {
        const result = await postJson<WriteResult>(`/api/result/${id}/actions`, {
          values: { action },
          expected_version: expectedVersion,
          request_key: requestKey,
        })
        if (result.ok) {
          clearFlightByValue(requestKey)
          this.adopt(result.entry)
        } else {
          clearFlightByValue(requestKey)
        }
        return result
      } catch (error) {
        if (error instanceof ApiConflictError) {
          clearFlightByValue(requestKey)
          this.adoptFromServer(error.current as ResultEntry)
        }
        throw error
      } finally {
        this.inFlight[flightKey] = false
      }
    },
  },
})
