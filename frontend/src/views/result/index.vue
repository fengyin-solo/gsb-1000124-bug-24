<template>
  <section class="page" data-module="result">
    <header class="page-head">
      <div>
        <h2>检测结果管理</h2>
        <p class="page-desc">维护检测结果，围绕结果编号、所属任务、检测项、实测值做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记检测结果</button>
        <button class="btn" type="button" @click="exportRows">导出检测结果清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>结果编号</span>
        <input v-model="keyword" placeholder="按结果编号检索" />
      </label>
      <label class="filter-item">
        <span>结果状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="option in statuses" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
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
          <td v-for="column in columns" :key="column">
            <button v-if="column === '结果编号'" class="link" type="button" @click="openEntry(row.id)">
              {{ row[column as Column] ?? '—' }}
            </button>
            <template v-else>{{ row[column as Column] ?? '—' }}</template>
          </td>
          <td class="row-actions">
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              :disabled="busyKey === `${row.id}:${action}`"
              @click="runAction(action, row)"
            >
              {{ busyKey === `${row.id}:${action}` ? '处理中…' : action }}
            </button>
            <button class="link" type="button" @click="openEntry(row.id)">录入/详情</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无检测结果数据，可先登记检测结果</td>
        </tr>
      </tbody>
    </table>

    <div v-if="conflictMessage" class="conflict-banner">
      <span>{{ conflictMessage }}</span>
      <button class="link" type="button" @click="reload">按最新内容刷新列表</button>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条检测结果记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiConflictError, type ResultEntry, useResultStore } from '@/stores/result'

const ENDPOINT = '/api/result'
const columns = ['结果编号', '所属任务', '检测项', '实测值', '标准限值', '判定结论', '检测日期', '结果状态'] as const
type Column = (typeof columns)[number]
const statuses = ['待录入', '已录入', '待审核', '已发布', '已作废']

const router = useRouter()
const resultStore = useResultStore()

const rows = computed(() => resultStore.listCache)
const total = computed(() => resultStore.listTotal)

const keyword = ref('')
const statusFilter = ref('')
const errorMessage = ref('')
const conflictMessage = ref('')
const busyKey = ref('')

const statCards = computed(() => [
  { label: '待录入结果', value: resultStore.statusCount('待录入') },
  { label: '待审核结果', value: resultStore.statusCount('待审核') },
  { label: '已发布结果', value: resultStore.statusCount('已发布') },
])

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  void router.push({ name: 'result-entry-create' })
}

function openEntry(id: number) {
  void router.push({ name: 'result-entry-edit', params: { id } })
}

/** 按当前状态只暴露合法动作，非法跳转在后端也会被拦下，双保险。 */
function availableActions(row: ResultEntry): string[] {
  switch (row.status) {
    case '待录入':
      return ['作废结果']
    case '已录入':
      return ['提交审核', '作废结果']
    case '待审核':
      return ['作废结果']
    default:
      return []
  }
}

async function runAction(action: string, row: ResultEntry) {
  errorMessage.value = ''
  conflictMessage.value = ''
  busyKey.value = `${row.id}:${action}`
  try {
    const result = await resultStore.runAction(row.id, action, row.version)
    if (!result.ok) {
      errorMessage.value = result.message
    }
  } catch (error) {
    if (error instanceof ApiConflictError) {
      conflictMessage.value =
        `${error.message}。列表已切换为服务端最新内容，请确认后再操作。`
    } else {
      errorMessage.value = error instanceof Error ? error.message : '检测结果操作失败'
    }
  } finally {
    busyKey.value = ''
  }
}

async function reload() {
  errorMessage.value = ''
  conflictMessage.value = ''
  try {
    await resultStore.fetchList({ keyword: keyword.value, status: statusFilter.value })
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '检测结果列表读取失败'
  }
}

onMounted(reload)
</script>
