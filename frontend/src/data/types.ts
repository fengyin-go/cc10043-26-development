/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
  entry?: EntryRow
}

/** 流式数据导出的单个文件版本：明细行与逐类合计来自同一次扫描快照。 */
export type ExportVersionManifest = {
  版本: number
  生成时间: string
  数据源类型: string
  API名称?: string
  时段开始?: string
  时段结束?: string
  数据源?: string
  调用编号范围?: string
  行数?: number
  成功?: number
  客户端错误?: number
  服务端错误?: number
  超时?: number
  总计?: number
  文件名称: string
  下载地址: string
}

/** 导出任务的版本清单：再次下载前可查每版使用的调用范围及行数。 */
export type ExportManifestResult = {
  任务ID: number
  任务名称: string
  status: string
  数据源类型: string
  API名称?: string
  时段开始?: string
  时段结束?: string
  当前版本: number
  调用编号范围: string
  行数: number
  下载地址: string
  versions: ExportVersionManifest[]
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}
