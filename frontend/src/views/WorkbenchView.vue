<template>
  <section class="page" :data-module="meta.key">
    <header class="page-head">
      <div>
        <h2>{{ meta.name }}</h2>
        <p class="page-desc">{{ meta.desc }}</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">
          登记{{ meta.entity }}
        </button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">
          导出{{ meta.name }}清单
        </button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <span v-for="item in statusSummary" :key="item.status" class="legend-item">
        {{ item.status }}：{{ item.count }}
      </span>
    </p>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <div v-if="meta.key === 'api_logger'" class="filter-bar export-panel" data-testid="log-export-panel">
      <span class="panel-title">导出调用日志</span>
      <label class="filter-item">
        <span>API名称</span>
        <input v-model="logExport.api" data-testid="log-export-api" placeholder="精确匹配，如 billing" />
      </label>
      <label class="filter-item">
        <span>时段开始</span>
        <input v-model="logExport.start" data-testid="log-export-start" placeholder="2026-11-23T10:00:00" />
      </label>
      <label class="filter-item">
        <span>时段结束</span>
        <input v-model="logExport.end" data-testid="log-export-end" placeholder="2026-11-23T10:30:00" />
      </label>
      <button class="btn" type="button" data-testid="create-log-export" @click="createLogExport">
        生成导出任务
      </button>
      <span v-if="logExportMessage" class="panel-message">{{ logExportMessage }}</span>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
            <template v-if="meta.key === 'stream_export'">
              <button
                v-if="row.当前版本"
                class="link"
                type="button"
                :data-testid="`download-${row.id}`"
                @click="downloadLatest(row)"
              >
                下载文件
              </button>
              <button
                class="link"
                type="button"
                :data-testid="`manifest-${row.id}`"
                @click="toggleManifest(row)"
              >
                版本信息
              </button>
            </template>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <div v-if="meta.key === 'stream_export' && manifest" class="manifest-panel" data-testid="manifest-panel">
      <h3>导出任务 #{{ manifest.任务ID }}「{{ manifest.任务名称 }}」文件版本</h3>
      <p class="manifest-summary">
        当前版本：v{{ manifest.当前版本 || '—' }}　调用编号范围：{{ manifest.调用编号范围 || '—' }}　行数：{{ manifest.行数 ?? '—' }}
      </p>
      <table v-if="manifest.versions.length" class="data-table">
        <thead>
          <tr>
            <th>版本</th>
            <th>生成时间</th>
            <th>调用编号范围</th>
            <th>行数</th>
            <th>成功</th>
            <th>客户端错误</th>
            <th>服务端错误</th>
            <th>超时</th>
            <th>下载</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="v in manifest.versions" :key="v.版本" :data-testid="`version-${v.版本}`">
            <td>v{{ v.版本 }}</td>
            <td>{{ v.生成时间 }}</td>
            <td>{{ v.调用编号范围 || '—' }}</td>
            <td>{{ v.行数 ?? '—' }}</td>
            <td>{{ v.成功 ?? '—' }}</td>
            <td>{{ v.客户端错误 ?? '—' }}</td>
            <td>{{ v.服务端错误 ?? '—' }}</td>
            <td>{{ v.超时 ?? '—' }}</td>
            <td>
              <button
                class="link"
                type="button"
                :data-testid="`download-${manifest.任务ID}-v${v.版本}`"
                @click="downloadVersion(manifest.任务ID, v.版本)"
              >
                下载 v{{ v.版本 }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="manifest-summary">尚未生成文件，可先执行「开始导出」。</p>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条{{ meta.name }}记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  createEntry,
  createLogExportTask,
  downloadEntries,
  downloadExportFile,
  fetchExportManifest,
  listEntries,
  moduleMeta,
  runAction as applyAction,
} from '@/api/backend-service'
import type { EntryRow, ExportManifestResult } from '@/data/types'

const props = defineProps<{ moduleKey: string }>()
const meta = moduleMeta(props.moduleKey)
const rows = ref<EntryRow[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = meta.fields.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const logExport = ref({ api: '', start: '', end: '' })
const logExportMessage = ref('')
const manifest = ref<ExportManifestResult | null>(null)
const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 1]).length,
  },
])
const statusSummary = computed(() =>
  meta.statuses.map((status) => ({
    status,
    count: rows.value.filter((row) => String(row.status) === status).length,
  })),
)

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  try {
    await downloadEntries(meta.key, filters.value)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(meta.fields.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const result = await createEntry(meta.key, createValues.value)
    if (!result.ok) throw new Error(result.message)
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: EntryRow) {
  errorMessage.value = ''
  try {
    const result = await applyAction(meta.key, Number(row.id), action)
    if (!result.ok) throw new Error(result.message)
    await reload()
    if (manifest.value && manifest.value.任务ID === Number(row.id)) {
      manifest.value = await fetchExportManifest(Number(row.id))
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function createLogExport() {
  logExportMessage.value = ''
  errorMessage.value = ''
  try {
    const result = await createLogExportTask({
      API名称: logExport.value.api.trim(),
      时段开始: logExport.value.start.trim(),
      时段结束: logExport.value.end.trim(),
    })
    if (!result.ok) throw new Error(result.message)
    logExportMessage.value = `${result.message}，请到「流式数据导出」页执行并下载`
  } catch (error) {
    logExportMessage.value = error instanceof Error ? error.message : '导出任务登记失败'
  }
}

function downloadLatest(row: EntryRow) {
  downloadExportFile(Number(row.id))
}

function downloadVersion(id: number, version: number) {
  downloadExportFile(id, version)
}

async function toggleManifest(row: EntryRow) {
  errorMessage.value = ''
  const id = Number(row.id)
  if (manifest.value && manifest.value.任务ID === id) {
    manifest.value = null
    return
  }
  try {
    manifest.value = await fetchExportManifest(id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '版本清单读取失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

onMounted(reload)
</script>
