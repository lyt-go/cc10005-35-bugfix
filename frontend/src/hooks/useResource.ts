import { useCallback, useEffect, useRef, useState } from 'react'

import { request } from '@/api/client'

/**
 * 列表与详情共用的取数链路。
 *
 * 解决三类旧问题：
 * - 旧请求晚返回覆盖新结果（结果错位）：每次请求带单调递增的序列号，只接受最新一次；
 * - 路由/筛选切换无法中断：AbortController 主动 abort，配合 request 原样抛出 AbortError；
 * - 失败后重试仍残留旧值：loading/empty/error 三态显式区分，重试走同一条链路。
 */
export type ResourceState<T> = {
  data: T | null
  loading: boolean
  error: string
  empty: boolean
  emptyReason: string
  retry: () => void
}

type PageEnvelope<T> = { items: T[]; total: number }

function emptyReasonFor(path: string, query: string): string {
  const params = new URLSearchParams(query)
  const keyword = params.get('keyword')
  const status = params.get('status')
  if (path.includes('/decisions')) {
    return '该规则暂无限速决策记录：启用并上报实测错误率后会在此保留决策（含原告警版本）'
  }
  const scope = path.includes('monitor') ? '监控面板' : path.includes('throttle') ? '限速规则' : '数据'
  if (keyword || status) {
    return `没有匹配的${scope}：当前筛选条件（${[keyword && `编号含「${keyword}」`, status && `状态「${status}」`]
      .filter(Boolean)
      .join('，')}）下无记录，可重置条件后重试`
  }
  return `暂无${scope}，可先登记新记录`
}

export function useResource<T = unknown>(
  path: string,
  query = '',
): ResourceState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [empty, setEmpty] = useState(false)
  const [reloadTick, setReloadTick] = useState(0)

  const abortRef = useRef<AbortController | null>(null)
  const seqRef = useRef(0)

  const url = query ? `${path}?${query}` : path

  useEffect(() => {
    const controller = new AbortController()
    abortRef.current?.abort()
    abortRef.current = controller
    const seq = ++seqRef.current

    setLoading(true)
    setError('')
    setEmpty(false)

    request(url, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`接口返回 ${response.status}，请稍后重试或检查筛选条件`)
        }
        const payload = (await response.json()) as T
        if (seq !== seqRef.current) return // 已有更新的请求发出，丢弃旧结果
        setData(payload)
        const items = (payload as PageEnvelope<unknown>).items
        setEmpty(Array.isArray(items) ? items.length === 0 : payload === null)
        setLoading(false)
      })
      .catch((caught: unknown) => {
        if (caught instanceof DOMException && caught.name === 'AbortError') return
        if (seq !== seqRef.current) return
        const reason = caught instanceof Error ? caught.message : '数据处理失败'
        setError(`数据加载失败：${reason}。可点击重试，中断的请求会自动恢复到最新条件`)
        setData(null)
        setEmpty(false)
        setLoading(false)
      })

    return () => controller.abort()
  }, [url, reloadTick])

  const retry = useCallback(() => setReloadTick((tick) => tick + 1), [])

  return {
    data,
    loading,
    error,
    empty,
    emptyReason: emptyReasonFor(path, query),
    retry,
  }
}
