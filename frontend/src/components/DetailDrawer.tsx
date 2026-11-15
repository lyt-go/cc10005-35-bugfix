import type { ReactNode } from 'react'

import { useResource } from '@/hooks/useResource'

import { BindingBadge, StatusBadge } from './StatusBadge'

export type DetailField = { label: string; key: string }

type Row = Record<string, string | number | boolean | null>

/**
 * 详情抽屉：与列表调用同一个 endpoint（同一条取数链路），
 * 内部同样走 useResource，切换记录时旧请求被中断、序列号保证不串号。
 */
export default function DetailDrawer({
  endpoint,
  entryId,
  title,
  fields,
  onClose,
  children,
}: {
  endpoint: string
  entryId: number
  title: string
  fields: DetailField[]
  onClose: () => void
  children?: ReactNode
}) {
  const { data, loading, error, retry } = useResource<Row>(endpoint, '')
  const detail = data

  return (
    <div className="drawer-mask" onClick={onClose}>
      <aside className="drawer" onClick={(event) => event.stopPropagation()}>
        <header className="drawer-head">
          <h3>{title}</h3>
          <button className="btn ghost" type="button" onClick={onClose}>关闭</button>
        </header>

        {loading && <p className="drawer-tip">正在读取详情，旧请求已中断…</p>}

        {!loading && error && (
          <div className="drawer-tip error-text">
            <p>{error}</p>
            <button className="btn" type="button" onClick={retry}>重试</button>
          </div>
        )}

        {!loading && !error && !detail && (
          <p className="drawer-tip">没有匹配的详情数据，记录可能已被删除或归档</p>
        )}

        {!loading && !error && detail && (
          <dl className="drawer-body">
            <div className="drawer-row">
              <dt>状态</dt>
              <dd><StatusBadge status={String(detail.status ?? '')} /></dd>
            </div>
            {fields.map((field) => (
              <div className="drawer-row" key={field.key}>
                <dt>{field.label}</dt>
                <dd>{detail[field.key] === null || detail[field.key] === undefined || detail[field.key] === '' ? '—' : String(detail[field.key])}</dd>
              </div>
            ))}
            {typeof detail['绑定失效'] === 'boolean' && (
              <div className="drawer-row">
                <dt>面板绑定</dt>
                <dd><BindingBadge invalid={Boolean(detail['绑定失效'])} /></dd>
              </div>
            )}
          </dl>
        )}

        {!loading && !error && detail && children}
      </aside>
    </div>
  )
}
