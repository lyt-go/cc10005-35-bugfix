import { useResource } from '@/hooks/useResource'

type Overview = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

const fallbackModules = [
  { name: '采集监控', created: 0, pending: 0, abnormal: 0 },
  { name: '动态限速', created: 0, pending: 0, abnormal: 0 },
]

export default function OverviewView() {
  const { data, loading, error, retry } = useResource<Overview>('/api/overview', '')
  const cards = data?.cards ?? []
  const moduleRows = data?.modules ?? fallbackModules

  return (
    <section className="page">
      <header className="page-head">
        <div>
          <h2>运营概览</h2>
          <p className="page-desc">汇总各业务模块的关键指标，先看总量再看异常。</p>
        </div>
      </header>

      {loading && <p className="state-tip">正在加载运营概览，旧请求已中断…</p>}

      {!loading && error && (
        <div className="state-tip error-text">
          <span>{error}</span>
          <button className="btn" type="button" onClick={retry}>重试</button>
        </div>
      )}

      {!loading && !error && (
        <>
          <div className="stat-row">
            {cards.map((card) => (
              <article key={card.label} className="stat-card">
                <span className="stat-label">{card.label}</span>
                <strong className="stat-value">{card.value}</strong>
              </article>
            ))}
            {cards.length === 0 && <p className="muted-text">暂无概览数据</p>}
          </div>
          <table className="data-table">
            <thead>
              <tr><th>业务模块</th><th>今日新增</th><th>待处理</th><th>异常量</th></tr>
            </thead>
            <tbody>
              {moduleRows.map((row) => (
                <tr key={row.name}>
                  <td>{row.name}</td>
                  <td>{row.created}</td>
                  <td>{row.pending}</td>
                  <td>{row.abnormal}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}
