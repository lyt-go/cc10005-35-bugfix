type BadgeTone = 'green' | 'amber' | 'red' | 'gray' | 'blue'

const TONE_CLASS: Record<BadgeTone, string> = {
  green: 'badge green',
  amber: 'badge amber',
  red: 'badge red',
  gray: 'badge gray',
  blue: 'badge blue',
}

const STATUS_TONE: Record<string, BadgeTone> = {
  已启用: 'green',
  面板评审中: 'amber',
  规则评审中: 'amber',
  待复核: 'red',
  草稿: 'blue',
  已归档: 'gray',
  已关闭: 'gray',
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={TONE_CLASS[STATUS_TONE[status] ?? 'gray']}>{status}</span>
}

export function BindingBadge({ invalid }: { invalid: boolean }) {
  return (
    <span className={TONE_CLASS[invalid ? 'red' : 'green']}>
      {invalid ? '绑定失效（待重新引用新面板版本）' : '绑定有效'}
    </span>
  )
}
