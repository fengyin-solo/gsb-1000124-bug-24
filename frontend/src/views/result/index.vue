<template>
  <section class="page" data-module="result">
    <header class="page-head">
      <div>
        <h2>检测结果管理</h2>
        <p class="page-desc">维护检测结果，围绕结果编号、所属任务、检测项、实测值做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" :disabled="isBusy" @click="openCreate">登记检测结果</button>
        <button class="btn" type="button" :disabled="isBusy" @click="exportRows">导出检测结果清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload({ abortExisting: true })">
      <label class="filter-item">
        <span>结果编号</span>
        <input v-model="filters.keyword" placeholder="按结果编号检索" />
      </label>
      <label class="filter-item">
        <span>结果状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <button class="btn" type="submit" :disabled="isBusy">查询</button>
      <button class="btn ghost" type="button" :disabled="isBusy" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ displayValue(row, column) }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :disabled="isBusy"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无检测结果数据，可先登记检测结果</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条检测结果记录</span>
      <span v-if="message" :class="messageType">{{ message }}</span>
    </footer>

    <div v-if="editorOpen" class="modal-mask" @click.self="closeEditor">
      <section class="modal-card" role="dialog" aria-modal="true" aria-labelledby="result-editor-title">
        <header class="modal-head">
          <div>
            <h3 id="result-editor-title">{{ editingId === null ? '登记检测结果' : '录入检测结果' }}</h3>
            <p v-if="editorDetail" class="modal-subtitle">
              ID {{ editorDetail.id }} · 当前状态：{{ editorDetail.status }} · 版本：{{ editorDetail.version }}
            </p>
          </div>
          <button class="btn" type="button" :disabled="saving" @click="closeEditor">关闭</button>
        </header>

        <p v-if="editorLoading" class="modal-tip">正在读取服务端最新明细……</p>
        <p v-else-if="!editorReadable" class="error-text">明细读取失败，未基于旧列表内容打开编辑窗口，避免继续提交过期版本。</p>

        <form v-if="editorReadable" class="editor-form" @submit.prevent="saveEntry">
          <label v-for="field in formFields" :key="field" class="editor-item">
            <span>{{ field }}<em v-if="requiredFields.includes(field)">*</em></span>
            <input
              v-model="form[field]"
              :type="field === '检测日期' ? 'date' : 'text'"
              :disabled="saving || !canEdit"
              :placeholder="`请输入${field}`"
            />
          </label>

          <p v-if="editorConflict" class="conflict-text">
            接口提示内容已被其他操作更新；表单和列表已切换为服务端最新版本，请基于当前内容调整后再保存。
          </p>
          <p v-else-if="!canEdit" class="modal-tip">当前状态仅可查看，不能继续修改。</p>

          <footer class="modal-actions">
            <button class="btn" type="button" :disabled="saving" @click="closeEditor">取消</button>
            <button class="btn primary" type="submit" :disabled="saving || !canEdit">
              {{ saving ? '保存中……' : '保存并重新读取' }}
            </button>
          </footer>
        </form>
      </section>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Entry = {
  id: number
  status: string
  version: number
  pending?: boolean
  abnormal?: boolean
  [key: string]: string | number | boolean | null | undefined
}

type ListPayload = {
  items?: Entry[]
  total?: number
}

type ActionPayload = {
  ok?: boolean
  message?: string
  entry?: Entry
}

const ENDPOINT = '/api/result'
const columns = ['结果编号', '所属任务', '检测项', '实测值', '标准限值', '判定结论', '检测日期', '结果状态']
const actions = ['录入结果', '提交审核', '作废结果']
const statuses = ['待录入', '已录入', '待审核', '已发布', '已作废']
const requiredFields = ['结果编号', '所属任务', '检测项']
const formFields = ['结果编号', '所属任务', '检测项', '实测值', '标准限值', '判定结论', '检测日期']
const editableStatuses = new Set(['待录入', '已录入', '待审核'])

const rows = ref<Entry[]>([])
const total = ref(0)
const message = ref('')
const messageType = ref('modal-tip')
const filters = reactive({ keyword: '', status: '' })

const editorOpen = ref(false)
const editorLoading = ref(false)
const editorReadable = ref(false)
const editingId = ref<number | null>(null)
const editorDetail = ref<Entry | null>(null)
const editorConflict = ref(false)
const saving = ref(false)
const actionKey = ref('')
const form = reactive<Record<string, string>>(Object.fromEntries(formFields.map((field) => [field, ''])))

const isBusy = computed(() => saving.value || Boolean(actionKey.value) || editorLoading.value)
const canEdit = computed(() => editorDetail.value !== null
  && (editingId.value === null || editableStatuses.has(editorDetail.value.status)))

const stats = computed(() => [
  { label: '待录入结果', value: countByStatus('待录入') },
  { label: '待审核结果', value: countByStatus('待审核') },
  { label: '已发布结果', value: countByStatus('已发布') },
])

let reloadController: AbortController | null = null
let reloadSeq = 0
let detailController: AbortController | null = null
let detailSeq = 0

function countByStatus(status: string): number {
  return rows.value.filter((row) => row.status === status).length
}

function displayValue(row: Entry, column: string): string | number | null {
  if (column === '结果状态') return row.status
  const value = row[column]
  return value === undefined || value === '' ? null : String(value)
}

function setMessage(text: string, type: 'modal-tip' | 'error-text' | 'success-text' = 'modal-tip') {
  message.value = text
  messageType.value = type
}

function abortReload() {
  reloadSeq += 1
  reloadController?.abort()
  reloadController = null
}

async function reload(options: { abortExisting?: boolean } = {}) {
  if (options.abortExisting) abortReload()
  const controller = new AbortController()
  reloadController = controller
  const seq = ++reloadSeq
  const params = new URLSearchParams()
  if (filters.keyword.trim()) params.set('keyword', filters.keyword.trim())
  if (filters.status) params.set('status', filters.status)

  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`, { signal: controller.signal })
    if (!response.ok) throw new Error('检测结果列表读取失败')
    const payload = (await response.json()) as ListPayload
    if (seq !== reloadSeq || controller.signal.aborted) return
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    if ((error as Error).name === 'AbortError' || controller.signal.aborted) return
    setMessage(error instanceof Error ? error.message : '检测结果列表读取失败', 'error-text')
  } finally {
    if (reloadController === controller) reloadController = null
  }
}

function resetFilters() {
  filters.keyword = ''
  filters.status = ''
  void reload({ abortExisting: true })
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  void openEditor(null)
}

async function openEditor(row: Entry | null) {
  if (detailController) detailController.abort()
  abortReload()
  const controller = new AbortController()
  detailController = controller
  const seq = ++detailSeq

  editorOpen.value = true
  editorLoading.value = true
  editorReadable.value = false
  editorConflict.value = false
  editingId.value = row?.id ?? null
  editorDetail.value = null
  formFields.forEach((field) => {
    form[field] = row ? String(row[field] ?? '') : ''
  })

  if (row === null) {
    if (detailController === controller) {
      detailController = null
      editorLoading.value = false
    }
    editorReadable.value = true
    return
  }

  try {
    const detail = await fetchDetail(row.id, controller.signal)
    if (seq !== detailSeq || controller.signal.aborted) return
    applyDetail(detail)
    syncListRow(detail)
  } catch (error) {
    if ((error as Error).name === 'AbortError' || controller.signal.aborted) return
    setMessage(error instanceof Error ? error.message : '检测结果详情读取失败', 'error-text')
  } finally {
    if (detailController === controller) {
      editorLoading.value = false
      detailController = null
    }
  }
}

function closeEditor() {
  if (saving.value) return
  if (detailController) detailController.abort()
  editorOpen.value = false
  editorDetail.value = null
  editorConflict.value = false
  editingId.value = null
}

async function fetchDetail(id: number, signal?: AbortSignal): Promise<Entry> {
  const response = await request(`${ENDPOINT}/${id}`, { signal })
  if (!response.ok) throw new Error(`检测结果详情读取失败（${response.status}）`)
  return (await response.json()) as Entry
}

function applyDetail(detail: Entry) {
  editorDetail.value = detail
  editorReadable.value = true
  editorLoading.value = false
  formFields.forEach((field) => {
    const value = detail[field]
    form[field] = value === null || value === undefined ? '' : String(value)
  })
}

function listFilterMatches(detail: Entry): boolean {
  const keyword = filters.keyword.trim()
  if (keyword && !String(detail['结果编号'] ?? '').includes(keyword)) return false
  if (filters.status && detail.status !== filters.status) return false
  return true
}

function syncListRow(detail: Entry, options: { canInsert?: boolean } = {}) {
  const index = rows.value.findIndex((row) => row.id === detail.id)
  const visible = listFilterMatches(detail)
  if (index >= 0) {
    if (visible) {
      rows.value[index] = detail
    } else {
      rows.value = rows.value.filter((row) => row.id !== detail.id)
      total.value = Math.max(total.value - 1, 0)
    }
  } else if (options.canInsert && visible) {
    rows.value = [detail, ...rows.value]
    total.value += 1
  }
}

function upsertListRow(detail: Entry) {
  syncListRow(detail, { canInsert: true })
}

async function readErrorCurrent(response: Response, fallbackId: number | null): Promise<Entry | null> {
  try {
    const payload = (await response.json()) as { current?: Entry; detail?: Entry }
    if (payload.current) return payload.current
  } catch {
    // 某些代理只返回文本错误；下面再按 ID 读取一次。
  }
  return fallbackId === null ? null : fetchDetail(fallbackId).catch(() => null)
}

async function saveEntry() {
  if (saving.value) return
  const id = editingId.value
  if (id !== null && (!editorDetail.value || !canEdit.value)) return

  const values = Object.fromEntries(formFields.map((field) => [field, form[field].trim()]))
  const missing = requiredFields.filter((field) => !values[field])
  if (missing.length) {
    setMessage(`缺少必填字段：${missing.join('、')}`, 'error-text')
    return
  }

  saving.value = true
  abortReload()
  setMessage('正在保存检测结果……')
  const url = id === null ? ENDPOINT : `${ENDPOINT}/${id}`
  const requestId = makeRequestId(id, editorDetail.value?.version, values)
  const body: Record<string, unknown> = { values, request_id: requestId }
  if (id !== null) body.expected_version = editorDetail.value?.version

  try {
    const response = await request(url, {
      method: id === null ? 'POST' : 'PUT',
      body: JSON.stringify(body),
    })

    if (response.status === 409) {
      const current = await readErrorCurrent(response, id)
      if (current) {
        applyDetail(current)
        syncListRow(current)
        editorConflict.value = true
      }
      throw new Error('检测结果已被其他人修改，已载入服务端最新版本')
    }

    if (!response.ok) throw new Error(`检测结果保存失败（${response.status}）`)
    const payload = (await response.json()) as ActionPayload
    if (payload.ok === false) throw new Error(payload.message || '检测结果保存失败')

    const saved = payload.entry
    if (!saved) throw new Error('保存成功但服务端未返回检测结果')

    // 保存先落库；服务端返回的是权威明细，列表立刻使用同一份事实。
    applyDetail(saved)
    upsertListRow(saved)
    editorOpen.value = false
    editorDetail.value = null
    editingId.value = null

    // 随后重新读取详情做一次核对；即使核对请求短暂失败，也不把界面回滚到旧列表。
    try {
      const verified = await fetchDetail(Number(saved.id))
      applyDetail(verified)
      upsertListRow(verified)
      setMessage('检测结果已保存，并已与服务端明细核对', 'success-text')
    } catch {
      setMessage('检测结果已保存；详情复核请求暂时失败，请稍后刷新核对', 'success-text')
    }
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '检测结果保存失败', 'error-text')
  } finally {
    saving.value = false
  }
}

async function runAction(action: string, row: Entry) {
  if (action === '录入结果') {
    await openEditor(row)
    return
  }
  if (isBusy.value) return

  const key = `${row.id}:${action}`
  actionKey.value = key
  abortReload()
  editorConflict.value = false
  setMessage(`正在执行「${action}」……`)

  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        values: { action },
        expected_version: row.version,
        request_id: makeRequestId(row.id, row.version, { action }),
      }),
    })

    if (response.status === 409) {
      const current = await readErrorCurrent(response, row.id)
      if (current) {
        syncListRow(current)
        if (editorOpen.value && editingId.value === current.id) applyDetail(current)
      }
      throw new Error('检测结果已被其他人修改，列表和详情已同步为最新状态')
    }

    if (!response.ok) throw new Error(`检测结果动作未生效（${response.status}）`)
    const payload = (await response.json()) as ActionPayload
    if (payload.ok === false) throw new Error(payload.message || '检测结果动作未生效')
    if (!payload.entry) throw new Error('动作已响应但服务端未返回检测结果')

    // 动作落库后先采用服务端返回明细，再读详情核对，确保列表不保留动作前状态。
    syncListRow(payload.entry)
    if (editorOpen.value && editingId.value === payload.entry.id) applyDetail(payload.entry)

    try {
      const verified = await fetchDetail(row.id)
      syncListRow(verified)
      if (editorOpen.value && editingId.value === verified.id) applyDetail(verified)
    } catch {
      // 权威变更已由动作响应给出；复核失败只提示，不回滚到旧状态。
    }
    setMessage(payload.message || '检测结果操作成功', 'success-text')
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '检测结果操作失败', 'error-text')
  } finally {
    if (actionKey.value === key) actionKey.value = ''
  }
}

function makeRequestId(id: number | null, version: number | undefined, values: Record<string, unknown>): string {
  const normalized = Object.entries(values)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, value]) => `${key}=${String(value ?? '')}`)
    .join('&')
  return `result:${id ?? 'new'}:${version ?? 0}:${stableHash(normalized)}`
}

function stableHash(text: string): number {
  let hash = 0
  for (let index = 0; index < text.length; index += 1) {
    hash = (hash << 5) - hash + text.charCodeAt(index)
    hash |= 0
  }
  return Math.abs(hash)
}

onMounted(() => {
  void reload()
})
</script>

<style scoped>
.page-actions {
  display: flex;
  gap: 8px;
}

.filter-item select,
.filter-item input {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
}

.modal-mask {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgb(15 23 42 / 45%);
  padding: 20px;
}

.modal-card {
  width: min(760px, 100%);
  max-height: 90vh;
  overflow: auto;
  background: #fff;
  border-radius: 10px;
  padding: 18px;
  box-shadow: 0 18px 45px rgb(15 23 42 / 25%);
}

.modal-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.modal-head h3 {
  margin: 0;
}

.modal-subtitle,
.modal-tip {
  color: var(--muted);
  font-size: 13px;
  margin: 4px 0 0;
}

.editor-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.editor-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
}

.editor-item em {
  color: #b42318;
  font-style: normal;
  margin-left: 2px;
}

.editor-item input {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
}

.conflict-text {
  grid-column: 1 / -1;
  color: #b54708;
  background: #fffaeb;
  border: 1px solid #fedf89;
  border-radius: 6px;
  padding: 8px 10px;
  margin: 0;
  font-size: 13px;
}

.success-text {
  color: #027a48;
}

.modal-actions {
  grid-column: 1 / -1;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
</style>
