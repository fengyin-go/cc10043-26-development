import { apiUrl, request } from './client'
import { moduleMeta } from '@/data/modules'
import type { ActionResult, EntryRow, ExportManifestResult, OverviewResult, PageResult } from '@/data/types'

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

/** 从 API 调用日志页登记一个调用日志导出任务，任务随带 API 与时段。 */
export async function createLogExportTask(values: {
  API名称: string
  时段开始: string
  时段结束: string
}): Promise<ActionResult> {
  const response = await request('/api/stream_export/log-task', {
    method: 'POST',
    body: JSON.stringify(values),
  })
  return await response.json()
}

/** 读取导出任务的版本清单：再次下载前可查调用范围与行数。 */
export async function fetchExportManifest(id: number): Promise<ExportManifestResult> {
  const response = await request(`/api/stream_export/${id}/manifest`)
  if (!response.ok) throw new Error('导出文件清单读取失败')
  return await response.json()
}

/** 下载某一版导出文件；缺省取最新版，地址与清单中的下载地址一致。 */
export function downloadExportFile(id: number, version?: number): void {
  const path = `/api/stream_export/${id}/download${version ? `?version=${version}` : ''}`
  const anchor = document.createElement('a')
  anchor.href = apiUrl(path)
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
}
