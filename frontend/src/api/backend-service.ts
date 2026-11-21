import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, OverviewResult, PageResult } from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ── 多资产质检批次：批次登记 / 批量提交 / 调度 / 补检 / 整组报告 ──

export type BatchMember = {
  资产编号: string
  资产名称: string
  资产类型: string
  到齐状态: string
  接入状态: string
  质检状态: string
  失败原因: string
  证据: Record<string, { 分区: string; 测点: Record<string, number>; 重复导入次数: number; 补检: boolean }>
}

export type QualityBatch = EntryRow & {
  批次编号: string
  资产组: string
  质量窗口: string
  到齐情况: string
  质检状态: string
  组结论: string
  可比结论: string
  成员: BatchMember[]
}

export type BatchActionResult = {
  ok: boolean
  message?: string
  detail?: string
  group_conclusion: string
  comparable: boolean
  missing_assets: string[]
  results?: { 资产编号: string; 分区?: string; ok: boolean; message: string; 去重?: boolean }[]
}

export type BatchReport = {
  ok: boolean
  批次编号: string
  资产组: string
  质量窗口: string
  到齐情况: string
  组结论: string
  可比结论: string
  comparable: boolean
  未到齐资产: string[]
  assets: {
    资产编号: string
    资产名称: string
    资产类型: string
    到齐状态: string
    质检状态: string
    分区: string[]
    补检: boolean
    来源资产: string
    来源窗口: string
    阈值下限: string | null
    实际测点: string | null
    判定: string
  }[]
  未结调度项: { 作业编号: string; status: string; 待办说明: string }[]
}

async function postBatch(path: string, body?: unknown): Promise<Record<string, unknown>> {
  const response = await request(path, {
    method: 'POST',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = (await response.json()) as Record<string, unknown>
  if (!response.ok) throw new Error(String(payload.detail ?? '质检批次操作失败'))
  return payload
}

export async function submitPartitions(
  batchId: number,
  submissions: Record<string, unknown>[],
): Promise<BatchActionResult> {
  return (await postBatch(`/api/quality_batch/${batchId}/submit`, { submissions })) as BatchActionResult
}

export async function scheduleBatch(batchId: number): Promise<BatchActionResult> {
  return (await postBatch(`/api/quality_batch/${batchId}/schedule`)) as BatchActionResult
}

export async function supplementPartition(
  batchId: number,
  payload: {
    asset_code: string
    partition: string
    source_asset: string
    source_window: string
    source_partition: string
    actual_value: number
    note?: string
  },
): Promise<Record<string, unknown>> {
  return await postBatch(`/api/quality_batch/${batchId}/supplement`, payload)
}

export async function loadBatchReport(batchId: number): Promise<BatchReport> {
  const response = await request(`/api/quality_batch/${batchId}/report`)
  if (!response.ok) throw new Error('整组质量报告读取失败')
  return (await response.json()) as BatchReport
}
