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

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in meta.fields" :key="column">{{ column }}</th>
          <th>当前状态</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :data-testid="`row-${row.id}`"
          :data-status="String(row.status)"
          :class="{ 'row-selected': selectedId === Number(row.id) }"
          @click="selectRow(row)"
        >
          <td v-for="column in meta.fields" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row.status }}</td>
          <td class="row-actions">
            <button
              v-for="action in meta.actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click.stop="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="meta.fields.length + 2" class="empty-state">
            暂无{{ meta.name }}数据，可先登记{{ meta.entity }}
          </td>
        </tr>
      </tbody>
    </table>

    <!-- 质检批次专属操作台：到齐情况、逐表规则、分区提交、补检与整组报告 -->
    <section v-if="isBatch && selected" class="batch-console" data-testid="batch-console">
      <header class="batch-head">
        <div>
          <h3>{{ selected.批次编号 }} · {{ selected.资产组 }} · {{ selected.质量窗口 }}</h3>
          <p class="page-desc">
            到齐 {{ selected.到齐情况 }} · {{ selected.组结论 }}
            <span :class="selected.可比结论 === '可比' ? 'tag-ok' : 'tag-warn'">
              {{ selected.可比结论 }}
            </span>
          </p>
        </div>
        <div class="page-actions">
          <button class="btn primary" data-testid="schedule-batch" type="button" :disabled="busy" @click="doSchedule">
            调度质检
          </button>
          <button class="btn" data-testid="load-report" type="button" :disabled="busy" @click="doLoadReport">
            整组质量报告
          </button>
        </div>
      </header>

      <table class="data-table batch-members">
        <thead>
          <tr>
            <th>资产</th><th>类型</th><th>到齐状态</th><th>质检状态</th>
            <th>分区证据（本表测点 / 重复导入）</th><th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="member in selected.成员" :key="member.资产编号" :data-testid="`member-${member.资产编号}`">
            <td>{{ member.资产名称 }}<small class="muted">（{{ member.资产编号 }}）</small></td>
            <td>{{ member.资产类型 }}</td>
            <td>
              <span :class="member.到齐状态 === '已到齐' ? 'tag-ok' : 'tag-warn'">{{ member.到齐状态 }}</span>
            </td>
            <td>{{ member.质检状态 }}</td>
            <td>
              <div v-for="(evidence, partition) in member.证据" :key="partition">
                {{ partition }}：{{ evidence.测点.完整性 }}
                <small class="muted">（重复导入 {{ evidence.重复导入次数 }} 次{{ evidence.补检 ? '，补检' : '' }}）</small>
              </div>
              <span v-if="!Object.keys(member.证据 || {}).length" class="muted">无</span>
            </td>
            <td>{{ member.失败原因 || '—' }}</td>
          </tr>
        </tbody>
      </table>

      <div class="batch-forms">
        <form class="analysis-form" data-testid="submit-form" @submit.prevent="doSubmit">
          <strong>批量提交分区</strong>
          <label>
            <span>资产编号</span>
            <select v-model="submitForm.asset">
              <option v-for="member in selected.成员" :key="member.资产编号" :value="member.资产编号">
                {{ member.资产名称 }}
              </option>
            </select>
          </label>
          <label>
            <span>分区</span>
            <input v-model="submitForm.partition" data-testid="submit-partition" />
          </label>
          <label>
            <span>完整性测点</span>
            <input v-model="submitForm.value" data-testid="submit-value" type="number" step="0.1" />
          </label>
          <label class="inline-check">
            <input v-model="submitForm.failIngest" type="checkbox" data-testid="submit-fail" />
            <span>模拟接入失败</span>
          </label>
          <button class="btn primary" data-testid="submit-batch" type="submit" :disabled="busy">提交</button>
        </form>

        <form class="analysis-form" data-testid="supplement-form" @submit.prevent="doSupplement">
          <strong>补检（需写明来源）</strong>
          <label>
            <span>资产编号</span>
            <select v-model="supplementForm.asset">
              <option v-for="member in selected.成员" :key="member.资产编号" :value="member.资产编号">
                {{ member.资产名称 }}
              </option>
            </select>
          </label>
          <label>
            <span>分区</span>
            <input v-model="supplementForm.partition" data-testid="supplement-partition" />
          </label>
          <label>
            <span>来源窗口</span>
            <input v-model="supplementForm.sourceWindow" data-testid="supplement-window" />
          </label>
          <label>
            <span>完整性测点</span>
            <input v-model="supplementForm.value" data-testid="supplement-value" type="number" step="0.1" />
          </label>
          <button class="btn primary" data-testid="supplement-batch" type="submit" :disabled="busy">补入</button>
        </form>
      </div>

      <p v-if="message" class="status-legend" data-testid="batch-message">{{ message }}</p>

      <div v-if="report" class="batch-report" data-testid="batch-report">
        <h4>三表关联质量报告 · {{ report.组结论 }}（{{ report.到齐情况 }}）</h4>
        <table class="data-table">
          <thead>
            <tr>
              <th>资产</th><th>判定</th><th>本表阈值</th><th>实际测点</th>
              <th>分区</th><th>补检</th><th>来源</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="asset in report.assets" :key="asset.资产编号" :data-testid="`report-${asset.资产编号}`">
              <td>{{ asset.资产名称 }}<small class="muted">（{{ asset.资产类型 }}）</small></td>
              <td>
                <span :class="asset.判定 === '通过' ? 'tag-ok' : 'tag-warn'">{{ asset.判定 }}</span>
              </td>
              <td>{{ asset.阈值下限 ?? '—' }}</td>
              <td>{{ asset.实际测点 ?? '—' }}</td>
              <td>{{ asset.分区.join('、') || '—' }}</td>
              <td>{{ asset.补检 ? `是（${asset.来源资产} / ${asset.来源窗口}）` : '否' }}</td>
              <td>{{ asset.来源窗口 || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="report.未结调度项.length" class="error-text">
          未结调度项：{{ report.未结调度项.map((item) => `${item.作业编号} ${item.待办说明}`).join('；') }}
        </p>
        <p v-else class="status-legend">无未结调度项。</p>
      </div>
    </section>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记{{ meta.entity }}</h3>
        <label v-for="field in meta.fields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
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
  downloadEntries,
  listEntries,
  loadBatchReport,
  moduleMeta,
  runAction as applyAction,
  scheduleBatch,
  submitPartitions,
  supplementPartition,
} from '@/api/backend-service'
import type { BatchReport, QualityBatch } from '@/api/backend-service'
import type { EntryRow } from '@/data/types'

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
const isBatch = meta.key === 'quality_batch'
const selected = ref<QualityBatch | null>(null)
const selectedId = ref<number | null>(null)
const report = ref<BatchReport | null>(null)
const message = ref('')
const busy = ref(false)
const submitForm = ref({ asset: '', partition: '', value: '', failIngest: false })
const supplementForm = ref({ asset: '', partition: '', sourceWindow: '', value: '' })

const stats = computed(() => [
  { label: meta.metrics[0], value: total.value },
  { label: meta.metrics[1], value: rows.value.filter((row) => row.status === meta.statuses[1]).length },
  {
    label: meta.metrics[2],
    value: rows.value.filter((row) => row.status === meta.statuses[meta.statuses.length - 2]).length,
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
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await listEntries(meta.key, filters.value)
    rows.value = payload.items
    total.value = payload.total
    if (isBatch) {
      const refreshed = payload.items.map((item) => item as QualityBatch)
      const current = refreshed.find((item) => Number(item.id) === selectedId.value) ?? refreshed[0]
      if (current) selectRow(current)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '业务列表读取失败'
  }
}

function selectRow(row: EntryRow) {
  if (!isBatch) return
  const batch = row as QualityBatch
  selectedId.value = Number(batch.id)
  selected.value = batch
  message.value = ''
  report.value = null
  const members = batch.成员 ?? []
  const firstMissing = members.find((member) => member.到齐状态 !== '已到齐') ?? members[0]
  const firstPartition = Object.keys(firstMissing?.证据 ?? {})[0] ?? 'dt=2026-11-21'
  submitForm.value = {
    asset: members[0]?.资产编号 ?? '',
    partition: firstPartition,
    value: '',
    failIngest: false,
  }
  supplementForm.value = {
    asset: firstMissing?.资产编号 ?? '',
    partition: firstPartition,
    sourceWindow: `${batch.质量窗口} 上午批次`,
    value: '',
  }
}

async function doSubmit() {
  if (!selected.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const measurement = { 完整性: Number(submitForm.value.value) }
    const submission: Record<string, unknown> = {
      资产编号: submitForm.value.asset,
      分区: submitForm.value.partition,
    }
    if (submitForm.value.failIngest) {
      submission.接入状态 = '接入失败'
      submission.失败原因 = '页面上报接入失败'
    } else {
      submission.metrics = measurement
    }
    const result = await submitPartitions(Number(selected.value.id), [submission])
    message.value = `${result.group_conclusion}：${result.results?.map((item) => item.message).join('；')}`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '批量提交失败'
  } finally {
    busy.value = false
  }
}

async function doSupplement() {
  if (!selected.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    await supplementPartition(Number(selected.value.id), {
      asset_code: supplementForm.value.asset,
      partition: supplementForm.value.partition,
      source_asset: supplementForm.value.asset,
      source_window: supplementForm.value.sourceWindow,
      source_partition: supplementForm.value.partition,
      actual_value: Number(supplementForm.value.value),
      note: '页面补检',
    })
    message.value = '补检已入账，补检来源已记录'
    await reload()
    await doLoadReport()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '补检失败'
  } finally {
    busy.value = false
  }
}

async function doSchedule() {
  if (!selected.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    const result = await scheduleBatch(Number(selected.value.id))
    message.value = `调度完成：${result.group_conclusion}，未到齐 ${result.missing_assets.join('、') || '无'}`
    await reload()
    await doLoadReport()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '调度失败'
  } finally {
    busy.value = false
  }
}

async function doLoadReport() {
  if (!selected.value) return
  try {
    report.value = await loadBatchReport(Number(selected.value.id))
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '整组报告读取失败'
  }
}

onMounted(reload)
</script>
