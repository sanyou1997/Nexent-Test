import { Fragment, ReactNode } from 'react'

/**
 * 轻量 Markdown 渲染（离网交付不引第三方依赖；全部走 React 元素，不使用 innerHTML，杜绝注入）。
 * 支持：标题、段落、有序/无序列表、引用、代码块、表格、分隔线、行内 **粗体** *斜体* `代码` [链接](url)。
 */

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*)/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const tok = m[0]
    const k = `${keyBase}-${i++}`
    if (tok.startsWith('**')) out.push(<strong key={k}>{tok.slice(2, -2)}</strong>)
    else if (tok.startsWith('`')) out.push(<code key={k} className="md-code">{tok.slice(1, -1)}</code>)
    else if (tok.startsWith('[')) {
      const mm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok)
      const href = mm?.[2] || ''
      const safe = /^(https?:|mailto:|\/)/i.test(href)
      out.push(safe
        ? <a key={k} href={href} target="_blank" rel="noopener noreferrer">{mm?.[1]}</a>
        : <span key={k}>{mm?.[1]}</span>)
    } else out.push(<em key={k}>{tok.slice(1, -1)}</em>)
    last = m.index + tok.length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

/** Nexent 知识库检索的引用标记（如 [[a5]]），平台不渲染引用卡片，显示前去掉；流式中途的半截标记一并去掉。 */
const CITE_MARK = /\[\[[a-zA-Z]?\d+\]\]/g
const CITE_TAIL = /\[\[[a-zA-Z]?\d*\]?$/

export function stripCitations(text: string): string {
  return (text || '').replace(CITE_MARK, '').replace(CITE_TAIL, '')
}

function splitRow(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
}

export default function Markdown({ text, className }: { text: string; className?: string }) {
  const lines = stripCitations(text).replace(/\r\n/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let i = 0
  let key = 0
  const k = () => `b${key++}`

  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    if (!trimmed) { i++; continue }

    // 代码块
    if (trimmed.startsWith('```')) {
      const buf: string[] = []
      i++
      while (i < lines.length && !lines[i].trim().startsWith('```')) { buf.push(lines[i]); i++ }
      i++
      blocks.push(<pre key={k()} className="md-pre"><code>{buf.join('\n')}</code></pre>)
      continue
    }
    // 标题
    const h = /^(#{1,6})\s+(.*)$/.exec(trimmed)
    if (h) {
      const level = Math.min(h[1].length + 1, 6)
      const Tag = `h${level}` as keyof JSX.IntrinsicElements
      blocks.push(<Tag key={k()} className="md-h">{inline(h[2], `h${key}`)}</Tag>)
      i++
      continue
    }
    // 分隔线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) { blocks.push(<hr key={k()} className="md-hr" />); i++; continue }
    // 表格
    if (trimmed.startsWith('|') && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const head = splitRow(trimmed)
      i += 2
      const rows: string[][] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) { rows.push(splitRow(lines[i])); i++ }
      const bk = k()
      blocks.push(
        <div key={bk} className="md-table-wrap">
          <table className="md-table">
            <thead><tr>{head.map((c, j) => <th key={j}>{inline(c, `${bk}h${j}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => (
              <tr key={ri}>{head.map((_, j) => <td key={j}>{inline(r[j] || '', `${bk}r${ri}c${j}`)}</td>)}</tr>
            ))}</tbody>
          </table>
        </div>,
      )
      continue
    }
    // 引用
    if (trimmed.startsWith('>')) {
      const buf: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('>')) { buf.push(lines[i].trim().replace(/^>\s?/, '')); i++ }
      const bk = k()
      blocks.push(<blockquote key={bk} className="md-quote">{buf.map((b, j) => <Fragment key={j}>{inline(b, `${bk}${j}`)}{j < buf.length - 1 && <br />}</Fragment>)}</blockquote>)
      continue
    }
    // 列表
    const isUl = (s: string) => /^\s*[-*+]\s+/.test(s)
    const isOl = (s: string) => /^\s*\d+[.)、]\s+/.test(s)
    if (isUl(line) || isOl(line)) {
      const ordered = isOl(line)
      const items: string[] = []
      while (i < lines.length && (ordered ? isOl(lines[i]) : isUl(lines[i]))) {
        let item = lines[i].replace(ordered ? /^\s*\d+[.)、]\s+/ : /^\s*[-*+]\s+/, '')
        i++
        // 续行（缩进且非新列表项）
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !isUl(lines[i]) && !isOl(lines[i])) {
          item += ' ' + lines[i].trim(); i++
        }
        items.push(item)
      }
      const bk = k()
      const L = ordered ? 'ol' : 'ul'
      blocks.push(<L key={bk} className="md-list">{items.map((it, j) => <li key={j}>{inline(it, `${bk}${j}`)}</li>)}</L>)
      continue
    }
    // 段落（合并连续非空行）
    const buf: string[] = []
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|```|>|\|)/.test(lines[i].trim())
      && !/^\s*([-*+]|\d+[.)、])\s+/.test(lines[i])) {
      buf.push(lines[i]); i++
    }
    if (!buf.length) { buf.push(lines[i]); i++ }
    const bk = k()
    blocks.push(<p key={bk} className="md-p">{buf.map((b, j) => <Fragment key={j}>{inline(b, `${bk}${j}`)}{j < buf.length - 1 && <br />}</Fragment>)}</p>)
  }
  return <div className={`md ${className || ''}`}>{blocks}</div>
}
