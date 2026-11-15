import { useCallback, useMemo, useState } from 'react'

import { postJson } from '@/api/client'
import DetailDrawer, { type DetailField } from '@/components/DetailDrawer'
import DecisionHistory from '@/components/DecisionHistory'
import { BindingBadge, StatusBadge } from '@/components/StatusBadge'
import { useResource } from '@/hooks/useResource'

const ENDPOINT = '/api/throttle'
const columns = [
  '规则编号',
  '目标域组',
  '初始速率',
  '最大速率',
  '加速因子',
  '减速因子',
  '错误率阈值',
  '降速恢复间隔',
]
const detailFields: DetailField[] = [
  { label: '绑定面板编号', key: '绑定面板编号' },
  { label: '绑定面板版本', key: '绑定面板版本' },
  { label: '绑定错误率阈值(%)', key: '绑定错误率阈值' },
  ...columns.map((key) => ({ label: key, key })),
]
const statuses = ['草稿', '规则评审中', '待复核', '已启用', '已关闭']
// 状态机：规则只能按 评审 -> 启用 -> 关闭 推进，关闭后必须重新评审并引用新面板版本
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  草稿: ['提交规则评审'],
  规则评审中: ['启用规则'],
  待复核: ['启用规则'],
  已启用: ['关闭规则'],
  已关闭: ['提交规则评审'],
}
const ACTIONS_NEED_PANEL = new Set(['启用规则'])

type Row = Record<string, string | number | boolean | null>
type Page = { items: Row[]; total: number }
type ActionResponse = { ok: boolean; message: string; entry: Row | null }

export default function ThrottleView() {
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('')
  const [applied, setApplied] = useState<Record<string, string>>({})
  const [refreshTick, setRefreshTick] = useState(0)
  const [actionError, setActionError] = useState('')
  const [actionInfo, setActionInfo] = useState('')
  const [actingId, setActingId] = useState<number | null>(null)
  const [detailId, setDetailId] = useState<number | null>(null)
  // 重新绑定表单：面板编号与错误率阈值；启用动作必须显式引用
  const [panelCode, setPanelCode] = useState('')
  const [panelRate, setPanelRate] = useState('')
  // 决策表单
  const [decisionRuleId, setDecisionRuleId] = useState('')
  const [observedRate, setObservedRate] = useState('')
  const [currentRate, setCurrentRate] = useState('')

  const query = useMemo(() => {
    const params = new URLSearchParams(applied)
    params.set('_t', String(refreshTick))
    return params.toString()
  }, [applied, refreshTick])

  const { data, loading, error, empty, emptyReason, retry } = useResource<Page>(ENDPOINT, query)
  const rows = data?.items ?? []
  const total = data?.total ?? 0

  const reload = useCallback(() => setRefreshTick((tick) => tick + 1), [])

  const enabledRows = useMemo(() => rows.filter((row) => row.status === '已启用'), [rows])

  const stats = useMemo(() => {
    const count = (name: string) => rows.filter((row) => row.status === name).length
    return [
      { label: '限速规则总数', value: total },
      { label: '已启用规则', value: count('已启用') },
      { label: '评审中/待复核', value: count('规则评审中') + count('待复核') },
      { label: '已关闭规则', value: count('已关闭') },
    ]
  }, [rows, total])

  function applyFilters() {
    const next: Record<string, string> = {}
    if (keyword.trim()) next.keyword = keyword.trim()
    if (status) next.status = status
    setApplied(next)
  }

  async function runAction(action: string, row: Row) {
    setActionError('')
    setActionInfo('')
    setActingId(Number(row.id))
    try {
      const values: Record<string, unknown> = { action }
      if (ACTIONS_NEED_PANEL.has(action)) {
        values['绑定面板编号'] = panelCode.trim()
        if (panelRate.trim() !== '') values['错误率阈值'] = panelRate.trim()
      }
      const result = await postJson<ActionResponse>(`${ENDPOINT}/${row.id}/actions`, { values })
      if (!result.ok) {
        setActionError(result.message)
        return
      }
      setActionInfo(result.message)
      setPanelCode('')
      setPanelRate('')
      reload()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : '动态限速操作失败，可重试')
    } finally {
      setActingId(null)
    }
  }

  async function recordDecision() {
    setActionError('')
    setActionInfo('')
    const target = enabledRows.find((row) => String(row.id) === decisionRuleId)
    if (!target) {
      setActionError('请先选择一条已启用的限速规则')
      return
    }
    try {
      const result = await postJson<ActionResponse>(`${ENDPOINT}/${target.id}/decisions`, {
        values: { 实测错误率: observedRate, 当前速率: currentRate },
      })
      if (!result.ok) {
        setActionError(result.message)
        return
      }
      setActionInfo(result.message)
      setObservedRate('')
      setCurrentRate('')
      reload()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : '限速决策失败，可重试')
    }
  }

  return (
    <section className="page" data-module="throttle">
      <header className="page-head">
        <div>
          <h2>动态限速管理</h2>
          <p className="page-desc">规则只能按 评审 → 启用 → 关闭 推进；引用的监控面板归档后进入待复核，重新启用必须绑定新面板编号与错误率阈值。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出动态限速清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); applyFilters() }}>
        <label className="filter-item">
          <span>规则编号</span>
          <input value={keyword} placeholder="按规则编号检索" onChange={(event) => setKeyword(event.target.value)} />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">全部状态</option>
            {statuses.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => { setKeyword(''); setStatus(''); setApplied({}) }}>重置条件</button>
      </form>

      <form className="filter-bar bind-bar" onSubmit={(event) => event.preventDefault()}>
        <label className="filter-item">
          <span>重新绑定面板编号（启用必填）</span>
          <input value={panelCode} placeholder="如 MONI-0003，仅接受已启用面板" onChange={(event) => setPanelCode(event.target.value)} />
        </label>
        <label className="filter-item">
          <span>错误率阈值%（须与面板一致）</span>
          <input value={panelRate} placeholder="留空则以面板版本阈值为准（0~100）" onChange={(event) => setPanelRate(event.target.value)} />
        </label>
      </form>

      {loading && <p className="state-tip">正在加载限速规则，旧请求已中断…</p>}

      {!loading && error && (
        <div className="state-tip error-text">
          <span>{error}</span>
          <button className="btn" type="button" onClick={retry}>重试</button>
        </div>
      )}

      {!loading && !error && (
        <table className="data-table">
          <thead>
            <tr>
              <th>状态</th>
              {columns.map((column) => <th key={column}>{column}</th>)}
              <th>绑定面板/版本</th>
              <th>绑定阈值</th>
              <th>绑定状态</th>
              <th>详情</th>
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.id)} className={row['绑定失效'] ? 'row-invalid' : ''}>
                <td><StatusBadge status={String(row.status ?? '')} /></td>
                {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
                <td>{row['绑定面板编号'] ? `${String(row['绑定面板编号'])} v${String(row['绑定面板版本'])}` : '—'}</td>
                <td>{row['绑定错误率阈值'] === null || row['绑定错误率阈值'] === undefined ? '—' : `${row['绑定错误率阈值']}%`}</td>
                <td><BindingBadge invalid={Boolean(row['绑定失效'])} /></td>
                <td>
                  <button className="link" type="button" onClick={() => setDetailId(Number(row.id))}>查看详情</button>
                </td>
                <td className="row-actions">
                  {(ACTIONS_BY_STATUS[String(row.status)] ?? []).map((action) => (
                    <button
                      key={action}
                      className="link"
                      type="button"
                      disabled={actingId === Number(row.id)}
                      onClick={() => void runAction(action, row)}
                    >
                      {actingId === Number(row.id) ? '处理中…' : action}
                    </button>
                  ))}
                  {(!ACTIONS_BY_STATUS[String(row.status)] || ACTIONS_BY_STATUS[String(row.status)].length === 0) && (
                    <span className="muted-text">无可用动作</span>
                  )}
                </td>
              </tr>
            ))}
            {empty && (
              <tr>
                <td className="empty-state" colSpan={columns.length + 6}>{emptyReason}</td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      {!loading && !error && enabledRows.length > 0 && (
        <form className="filter-bar decision-bar" onSubmit={(event) => { event.preventDefault(); void recordDecision() }}>
          <label className="filter-item">
            <span>目标规则</span>
            <select value={decisionRuleId} onChange={(event) => setDecisionRuleId(event.target.value)}>
              <option value="">请选择已启用规则</option>
              {enabledRows.map((row) => (
                <option key={String(row.id)} value={String(row.id)}>
                  {String(row['规则编号'])}（{String(row['绑定面板编号'])} v{String(row['绑定面板版本'])}，阈值 {String(row['绑定错误率阈值'])}%）
                </option>
              ))}
            </select>
          </label>
          <label className="filter-item">
            <span>实测错误率%（0~100，边界值同样参与决策）</span>
            <input value={observedRate} onChange={(event) => setObservedRate(event.target.value)} placeholder="如 5.0" />
          </label>
          <label className="filter-item">
            <span>当前速率（≥0）</span>
            <input value={currentRate} onChange={(event) => setCurrentRate(event.target.value)} placeholder="如 100" />
          </label>
          <button className="btn primary" type="submit">执行限速决策</button>
        </form>
      )}

      <footer className="page-foot">
        <span>共 {total} 条动态限速记录</span>
        {actionInfo && <span className="info-text">{actionInfo}</span>}
        {actionError && <span className="error-text">{actionError}</span>}
      </footer>

      {detailId !== null && (
        <DetailDrawer
          endpoint={`${ENDPOINT}/${detailId}`}
          entryId={detailId}
          title="限速规则详情"
          fields={detailFields}
          onClose={() => setDetailId(null)}
        >
          <DecisionHistory endpoint={ENDPOINT} entryId={detailId} />
        </DetailDrawer>
      )}
    </section>
  )
}
