import { useResource } from '@/hooks/useResource'

type Decision = {
  id: number
  规则编号: string
  面板编号: string
  面板版本: number
  错误率阈值: number
  实测错误率: number
  决策结果: string
  决策速率: number
  决策时间: string
}

type Envelope = { items: Decision[]; total: number }

/** 限速决策历史：只追加不改写，每条都保留决策当时的告警面板版本。 */
export default function DecisionHistory({ endpoint, entryId }: { endpoint: string; entryId: number }) {
  const { data, loading, error, empty, emptyReason, retry } = useResource<Envelope>(
    `${endpoint}/${entryId}/decisions`,
    '',
  )

  return (
    <section className="decision-box">
      <h4>限速决策历史（保留原告警版本）</h4>
      {loading && <p className="drawer-tip">决策记录加载中…</p>}
      {!loading && error && (
        <p className="drawer-tip error-text">
          {error}
          <button className="btn" type="button" onClick={retry}>重试</button>
        </p>
      )}
      {!loading && !error && empty && <p className="drawer-tip">{emptyReason}</p>}
      {!loading && !error && data && data.items.length > 0 && (
        <table className="data-table compact">
          <thead>
            <tr>
              <th>决策时间</th>
              <th>面板版本</th>
              <th>阈值%</th>
              <th>实测%</th>
              <th>决策</th>
              <th>速率</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((item) => (
              <tr key={item.id}>
                <td>{item.决策时间}</td>
                <td>{item.面板编号} v{item.面板版本}</td>
                <td>{item.错误率阈值}</td>
                <td>{item.实测错误率}</td>
                <td>{item.决策结果}</td>
                <td>{item.决策速率}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
