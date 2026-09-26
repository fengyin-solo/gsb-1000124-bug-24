<template>
  <section class="page" data-module="result-entry">
    <header class="page-head">
      <div>
        <h2>{{ isCreate ? '登记检测结果' : '检测结果录入' }}</h2>
        <p class="page-desc">
          维护结果编号、所属任务、检测项与实测值。保存以服务端返回为准，重新进入读取的是同一份内容。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn ghost" type="button" @click="goBack">返回列表</button>
      </div>
    </header>

    <div v-if="loading" class="empty-state">正在读取最新检测结果…</div>
    <div v-else-if="loadError" class="empty-state">
      <p>{{ loadError }}</p>
      <button v-if="!isCreate" class="btn" type="button" @click="loadDetail">重新读取</button>
    </div>

    <form v-else class="entry-form" @submit.prevent="save(true)">
      <div v-if="conflictMessage" class="conflict-banner">
        <span>{{ conflictMessage }}</span>
        <button class="link" type="button" @click="refreshAfterConflict">按最新内容重读</button>
      </div>

      <div class="detail-meta">
        <span v-if="!isCreate">记录编号：#{{ form.id }}</span>
        <span>当前状态：<strong>{{ loadedStatus }}</strong></span>
        <span v-if="!isCreate">数据版本：v{{ form.version }}</span>
        <span v-if="locked" class="error-text">该状态下录入内容已锁定，仅供查看</span>
      </div>

      <label v-for="field in editableFields" :key="field" class="form-item">
        <span>{{ field }}</span>
        <input v-model="form[field]" :disabled="locked || saving" :placeholder="`请输入${field}`" />
      </label>

      <footer class="form-foot">
        <span v-if="message" :class="lastOk ? 'success-text' : 'error-text'">{{ message }}</span>
        <span class="foot-spacer" />
        <button
          class="btn"
          type="button"
          :disabled="locked || saving || !dirty"
          @click="save(false)"
        >
          {{ saving ? '保存中…' : '暂存' }}
        </button>
        <button
          class="btn primary"
          type="submit"
          :disabled="locked || saving || (!isCreate && !dirty)"
        >
          {{ saving ? '保存中…' : '保存并完成录入' }}
        </button>
      </footer>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'

import { ApiConflictError, type ResultEntry, useResultStore } from '@/stores/result'

type EditableField =
  | '结果编号'
  | '所属任务'
  | '检测项'
  | '实测值'
  | '标准限值'
  | '判定结论'
  | '检测日期'

const editableFields: EditableField[] = [
  '结果编号',
  '所属任务',
  '检测项',
  '实测值',
  '标准限值',
  '判定结论',
  '检测日期',
]

type FormState = {
  id: number | null
  version: number
  结果编号: string
  所属任务: string
  检测项: string
  实测值: string
  标准限值: string
  判定结论: string
  检测日期: string
}

const emptyForm = (): FormState => ({
  id: null,
  version: 0,
  结果编号: '',
  所属任务: '',
  检测项: '',
  实测值: '',
  标准限值: '',
  判定结论: '',
  检测日期: '',
})

const route = useRoute()
const router = useRouter()
const store = useResultStore()

const entryId = computed(() => {
  const raw = route.params.id
  if (!raw) return null
  const id = Number(Array.isArray(raw) ? raw[0] : raw)
  return Number.isFinite(id) && id > 0 ? id : null
})
const isCreate = computed(() => entryId.value === null)

const form = reactive<FormState>(emptyForm())
const snapshot = ref<string>('')
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const message = ref('')
const lastOk = ref(false)
const conflictMessage = ref('')
const loadedStatus = ref('待录入')

const LOCKED_STATUSES = new Set(['待审核', '已发布', '已作废'])
const locked = computed(() => LOCKED_STATUSES.has(loadedStatus.value))

const dirty = computed(() => JSON.stringify(formValues()) !== snapshot.value)

function formValues(): Record<EditableField, string> {
  return {
    结果编号: form.结果编号,
    所属任务: form.所属任务,
    检测项: form.检测项,
    实测值: form.实测值,
    标准限值: form.标准限值,
    判定结论: form.判定结论,
    检测日期: form.检测日期,
  }
}

function hydrate(entry: ResultEntry) {
  form.id = entry.id
  form.version = entry.version
  for (const field of editableFields) {
    form[field] = entry[field] ?? ''
  }
  loadedStatus.value = entry.status
  snapshot.value = JSON.stringify(formValues())
  conflictMessage.value = ''
  message.value = ''
}

async function loadDetail() {
  if (entryId.value === null) return
  loading.value = true
  loadError.value = ''
  try {
    hydrate(await store.fetchDetail(entryId.value))
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '检测结果读取失败'
  } finally {
    loading.value = false
  }
}

/** 409 冲突后：以详情接口重新读取服务端最新事实，再让用户决定是否继续。 */
async function refreshAfterConflict() {
  if (
    dirty.value &&
    !window.confirm('重读会用服务端最新内容覆盖当前表单（本地未保存的修改将丢失），是否继续？')
  ) {
    return
  }
  conflictMessage.value = ''
  await loadDetail()
}

function goBack() {
  void router.push({ name: 'result' })
}

// 未保存的修改不允许静默丢失，避免"看起来保存了其实回退"的困惑。
onBeforeRouteLeave(() => {
  if (!saving.value && dirty.value && !locked.value) {
    return window.confirm('当前录入内容尚未保存，离开将放弃这些修改，确定离开吗？')
  }
  return true
})

function setFeedback(text: string, ok: boolean) {
  message.value = text
  lastOk.value = ok
}

async function save(complete: boolean) {
  if (saving.value || locked.value) return
  conflictMessage.value = ''
  message.value = ''

  // 前端先做一道必填提示，真正的口径仍由后端校验。
  const required: EditableField[] = complete
    ? ['结果编号', '所属任务', '检测项', '实测值']
    : ['结果编号', '所属任务', '检测项']
  const missing = required.filter((field) => !form[field].trim())
  if (missing.length) {
    setFeedback(`请先填写：${missing.join('、')}`, false)
    return
  }

  saving.value = true
  try {
    if (isCreate.value) {
      const result = await store.create({ ...formValues() })
      if (!result.ok) {
        setFeedback(result.message, false)
        return
      }
      setFeedback(result.message, true)
      // 创建成功后进入详情态：后续保存都带版本号，防止重复登记。
      if (result.entry) {
        hydrate(result.entry)
        router.replace({ name: 'result-entry-edit', params: { id: result.entry.id } })
      }
      await store.fetchList()
      return
    }

    const result = await store.save(
      entryId.value as number,
      { ...formValues(), complete },
      form.version,
    )
    if (!result.ok) {
      setFeedback(result.message, false)
      return
    }
    if (result.entry) hydrate(result.entry)
    setFeedback(result.message, true)
    // 保存成功后立刻回读一次列表，保证列表与详情反映同一事实
    // （adopt 已即时更新当前列表缓存，这里再向服务端确认全量口径）。
    await store.fetchList()
  } catch (error) {
    if (error instanceof ApiConflictError) {
      conflictMessage.value =
        `${error.message}。界面已暂存服务端最新版本（v${(error.current as ResultEntry).version}），` +
        '重新读取后如仍需修改请再保存，避免覆盖成两份分叉内容。'
      setFeedback('保存未生效：记录已被其他操作修改', false)
    } else {
      setFeedback(error instanceof Error ? error.message : '保存失败，请稍后重试', false)
    }
  } finally {
    saving.value = false
  }
}

onMounted(loadDetail)
</script>
