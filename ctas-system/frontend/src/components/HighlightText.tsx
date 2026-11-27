/** 关键词高亮：terms 去掉互相包含的短词后，长词优先包进 <mark>，避免嵌套混乱。
 *  类案检索（历史报告库 / 审理起草页）共用：让用户一眼看到「哪几个词命中」。 */
export default function HighlightText({ text, terms }: { text: string; terms: string[] }) {
  const raw = Array.from(new Set((terms || []).filter((t) => t && t.trim())))
  const uniq = raw
    .filter((t, _, arr) => !arr.some((o) => o !== t && o.includes(t)))
    .sort((a, b) => b.length - a.length)
  if (!uniq.length || !text) return <>{text}</>
  const escaped = uniq.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')
  const parts = text.split(new RegExp(`(${escaped})`, 'g'))
  return (
    <>
      {parts.map((p, i) => (uniq.includes(p)
        ? <mark key={i} style={{ padding: 0, background: '#fff7ed', color: 'inherit' }}>{p}</mark>
        : <span key={i}>{p}</span>))}
    </>
  )
}
