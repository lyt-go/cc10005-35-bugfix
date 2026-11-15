import { useCallback, useMemo, useState } from 'react'

import { postJson } from '@/api/client'
import DetailDrawer, { type DetailField } from '@/components/DetailDrawer'
import { StatusBadge } from '@/components/StatusBadge'
import { useResource } from '@/hooks/useResource'

const ENDPOINT = '/api/monitor'
const columns = [
  '面板编号',
  '监控指标集',
  '采样间隔秒',
  '趋势窗口分',
  '异常检测方法',
  '告警触发条件',
  '通知接收组',
  '自动降级开关',
]
const detailFields: DetailField[] = [
  { label: '面板版本', key: '面板版本' },
  { label: '错误率阈值(%)', key: '错误率阈值' },
  { label: '采样中', key: '采样中' },
  { label: '最后告警窗口', key: '最后告警窗口' },
  { label: '最后通知组', key: '最后通知组' },
  ...columns.map((key) => ({ label: key, key })),
]
const statuses = ['草稿', '面板评审中', '已启用', '已归档']
// 状态机：只渲染当前状态允许的下一个动作，从入口杜绝跳级提交
const ACTIONS_BY_STATUS: Record<string, string[]> = {
  草稿: ['提交面板评审'],
  面板评审中: ['启用面板'],
  已启用: ['归档面板'],
  已归档: [],
}

type Row = Record<string, string | number | boolean | null>
type Page = { items: Row[]; total: number }
type ActionResponse = { ok: boolean; message: string; entry: Row | null; related: Row[] }

export default function MonitorView() {
  const [keyword, setKeyword] = useState('')
  const [status, setStatus] = useState('')
  const [applied, setApplied] = useState<Record<string, string>>({})
  const [refreshTick, setRefreshTick] = useState(0)
  const [actionError, setActionError] = useState('')
  const [actingId, setActingId] = useState<number | null>(null)
  const [detailId, setDetailId] = useState<number | null>(null)

  const query = useMemo(() => {
    const params = new URLSearchParams(applied)
    params.set('_t', String(refreshTick))
    return params.toString()
  }, [applied, refreshTick])

  const { data, loading, error, empty, emptyReason, retry } = useResource<Page>(ENDPOINT, query)
  const rows = data?.items ?? []
  const total = data?.total ?? 0

  const reload = useCallback(() => setRefreshTick((tick) => tick + 1), [])

  const stats = useMemo(() => {
    const enabled = rows.filter((row) => row.status === '已启用').length
    const reviewing = rows.filter((row) => row.status === '面板评审中').length
    const archived = rows.filter((row) => row.status === '已归档').length
    return [
      { label: '监控面板总数', value: total },
      { label: '已启用面板', value: enabled },
      { label: '评审中面板', value: reviewing },
      { label: '已归档面板', value: archived },
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
    setActingId(Number(row.id))
    try {
      const result = await postJson<ActionResponse>(`${ENDPOINT}/${row.id}/actions`, {
        values: { action },
      })
      if (!result.ok) {
        // 后端业务拒绝（跳级/终态/绑定失败）必须把原因展示出来，不能静默留在旧状态
        setActionError(result.message)
        return
      }
      if (result.related.length > 0) {
        setActionError(`${result.message}（已联动限速规则 ${result.related.length} 条，限速列表同步更新）`)
      }
      reload()
    } catch (caught) {
      setActionError(caught instanceof Error ? caught.message : '采集监控操作失败，可重试')
    } finally {
      setActingId(null)
    }
  }

  return (
    <section className="page" data-module="monitor">
      <header className="page-head">
        <div>
          <h2>采集监控管理</h2>
          <p className="page-desc">配置采集运行状态的实时监控面板；归档面板停止采样与新告警，并联动限速规则进入待复核。</p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出采集监控清单
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
          <span>面板编号</span>
          <input value={keyword} placeholder="按面板编号检索" onChange={(event) => setKeyword(event.target.value)} />
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

      {loading && <p className="state-tip">正在加载监控面板，旧请求已中断…</p>}

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
              <th>面板版本</th>
              <th>错误率阈值</th>
              <th>采样中</th>
              <th>详情</th>
              <th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.id)} className={row.status === '已归档' ? 'row-archived' : ''}>
                <td><StatusBadge status={String(row.status ?? '')} /></td>
                {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
                <td>v{String(row['面板版本'] ?? 1)}</td>
                <td>{row['错误率阈值'] === null || row['错误率阈值'] === undefined ? '—' : `${row['错误率阈值']}%`}</td>
                <td>{row['采样中'] ? '采样中' : '已停止'}</td>
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
                    <span className="muted-text">终态，无可用动作</span>
                  )}
                </td>
              </tr>
            ))}
            {empty && (
              <tr>
                <td className="empty-state" colSpan={columns.length + 5}>{emptyReason}</td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      <footer className="page-foot">
        <span>共 {total} 条采集监控记录</span>
        {actionError && <span className="error-text">{actionError}</span>}
      </footer>

      {detailId !== null && (
        <DetailDrawer
          endpoint={`${ENDPOINT}/${detailId}`}
          entryId={detailId}
          title="监控面板详情"
          fields={detailFields}
          onClose={() => setDetailId(null)}
        />
      )}
    </section>
  )
}
