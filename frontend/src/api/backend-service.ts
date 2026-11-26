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

// ---- 日报更正 / 回补对账 ----

export type ReadingReference = {
  指标读数编号: string
  统计窗口?: string
  隔离率?: string
  隔离行分子?: string
  隔离行分母?: string
  发布状态?: string
  status?: string
  selectable?: boolean
}

export type LedgerReference = {
  对账编号: string
  原始行号: string
  关联批次: string
  回补动作: string
  隔离行分子?: string
  隔离行分母?: string
  隔离行状态?: string
  对账状态?: string
  关联更正编号?: string
}

export type CoverageAction = {
  对账编号: string
  回补动作: string
  关联批次?: string
  原始行号?: string
  回补结果?: string
  对账状态?: string
  可发起更正?: boolean
  对账时间?: string
  隔离行分子?: string
  隔离行分母?: string
}

export type ReviewBoard = {
  channel: string
  published_reports: EntryRow[]
  candidates: EntryRow[]
  claimed_candidates: EntryRow[]
  requarantined: CoverageAction[]
  pending_ledger: LedgerReference[]
}

export type CorrectionEntry = {
  quarantine_no: string
  row_no: string
  channel: string
  reading_candidates: ReadingReference[]
  ledger_rows: LedgerReference[]
  ledger_history: CoverageAction[]
}

export type CoverageDetail = {
  ok: boolean
  message: string
  更正编号?: string
  关联读数编号?: string
  关联批次?: string
  原始行号?: string
  更正前分子分母?: string
  更正后分子分母?: string
  covered_actions: CoverageAction[]
  covered_count: number
  uncovered_actions: CoverageAction[]
}

async function readJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in payload
      ? String((payload as { detail: unknown }).detail)
      : `接口返回 ${response.status}`
    throw new Error(detail)
  }
  return payload as T
}

export async function loadCorrectionEntry(quarantineNo: string): Promise<CorrectionEntry> {
  return readJson<CorrectionEntry>(
    `/api/workflow/quarantine/${encodeURIComponent(quarantineNo)}/correction-entry`)
}

export async function loadReviewBoard(channel = ''): Promise<ReviewBoard> {
  const query = channel ? `?channel=${encodeURIComponent(channel)}` : ''
  return readJson<ReviewBoard>(`/api/workflow/review-board${query}`)
}

export async function submitCorrection(payload: {
  correction_no: string
  reading_no: string
  ledger_nos: string[]
  operator: string
  remark?: string
  submit_time?: string
}): Promise<ActionResult> {
  return readJson<ActionResult>('/api/workflow/corrections', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function claimCorrection(
  correctionNo: string,
  reviewer: string,
): Promise<ActionResult> {
  return readJson<ActionResult>(
    `/api/workflow/corrections/${encodeURIComponent(correctionNo)}/claim`,
    { method: 'POST', body: JSON.stringify({ reviewer }) },
  )
}

export async function publishCorrection(
  correctionNo: string,
  reviewer: string,
): Promise<ActionResult> {
  return readJson<ActionResult>(
    `/api/workflow/corrections/${encodeURIComponent(correctionNo)}/publish`,
    { method: 'POST', body: JSON.stringify({ reviewer }) },
  )
}

export async function loadCorrectionCoverage(correctionNo: string): Promise<CoverageDetail> {
  return readJson<CoverageDetail>(
    `/api/workflow/corrections/${encodeURIComponent(correctionNo)}/coverage`)
}

export async function loadLedgerCoverage(ledgerNo: string): Promise<CoverageDetail> {
  return readJson<CoverageDetail>(
    `/api/workflow/ledger/${encodeURIComponent(ledgerNo)}/coverage`)
}

export type ReadingDetail = {
  ok: boolean
  message: string
  reading: EntryRow
  channel: string
  published_reports: EntryRow[]
  ledger_rows: LedgerReference[]
  available_ledger: LedgerReference[]
  history: EntryRow[]
}

export async function loadReadingDetail(readingNo: string): Promise<ReadingDetail> {
  return readJson<ReadingDetail>(
    `/api/workflow/readings/${encodeURIComponent(readingNo)}/detail`)
}

export async function requarantineRow(
  quarantineNo: string,
  reason: string,
): Promise<ActionResult> {
  return readJson<ActionResult>(
    `/api/workflow/quarantine/${encodeURIComponent(quarantineNo)}/requarantine`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  )
}
