// Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.

const numeral = "[〇零一二三四五六七八九十百千两0-9]+";
const articleHeading = new RegExp("^第(" + numeral + ")条(?=\\s|$)");
const itemHeading = new RegExp("^[（(](" + numeral + ")[）)]");
export type Citation = { article: number; paragraph?: number; item?: number; label: string };

function ordinal(text: string): number {
  if (/^\d+$/.test(text)) return Number(text);
  const units: Record<string, number> = { 十: 10, 百: 100, 千: 1000 };
  let total = 0, digit = 0;
  for (const char of text.replace(/两/g, "二").replace(/〇/g, "零")) {
    if (units[char]) {
      total += (digit || 1) * units[char];
      digit = 0;
    } else digit = "零一二三四五六七八九".indexOf(char);
  }
  return total + digit;
}

export function citationsIn(label: string): Citation[] {
  const pattern = new RegExp("第(" + numeral + ")条(?:第(" + numeral + ")款)?(?:第?[（(]?(" + numeral + ")[）)]?项)?", "g");
  return [...label.matchAll(pattern)].map(([text, a, p, i]) => ({
    article: ordinal(a), paragraph: p ? ordinal(p) : undefined,
    item: i ? ordinal(i) : undefined, label: text,
  }));
}

export function isLawObject(key: string): boolean {
  return /^(?:attachments|knowledge_base)\/.+\.txt$/i.test(key)
    && !/[\u0000-\u001f\\?#]/.test(key)
    && !key.split("/").some(part => !part || part === "." || part === "..");
}

export function locateLawSource(text: string, label: string) {
  const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/);
  const heading = lines.find(l => l.trim())?.trim() || "";
  const version = lines.find(l => l.startsWith("版本："))?.slice(3).trim() || "";
  const effective = lines.find(l => l.startsWith("施行日期："))?.slice(5).trim() || "";
  const targets = citationsIn(label);
  const spans = new Map<number, number[][]>();
  let article: number | undefined;
  let paragraphs: number[][] = [];
  const duplicates = new Set<number>();
  lines.forEach((raw, index) => {
    const line = raw.trim();
    if (!line) return;
    if (/^法规定位：/.test(line) || /^第[〇零一二三四五六七八九十百千0-9]+[章节编]/.test(line)) {
      article = undefined;
      return;
    }
    const match = articleHeading.exec(line);
    if (match) {
      article = ordinal(match[1]);
      if (spans.has(article)) duplicates.add(article);
      paragraphs = [[index]];
      spans.set(article, paragraphs);
    } else if (article !== undefined) {
      if (itemHeading.test(line)) {
        paragraphs[paragraphs.length - 1].push(index);
      } else {
        // Official text exports store each substantive paragraph on its own line.
        paragraphs.push([index]);
      }
    }
  });

  const title = label.match(/《([^》]+)》/)?.[1];
  const year = label.match(/[（(]((?:19|20)\d{2})年/)?.[1];
  const conflicting = Boolean((version && year && !version.includes(year)) ||
    (version && title && !heading.includes(title) && !title.includes(heading)));
  const highlights = new Map<number, number[]>();
  for (const [idx, target] of targets.entries()) {
    const parts = spans.get(target.article);
    if (!parts || duplicates.has(target.article) || conflicting) continue;
    let selected = target.paragraph ? parts[target.paragraph - 1] || [] : parts.flat();
    if (target.item) {
      const items = selected.filter(i => {
        const match = itemHeading.exec(lines[i].trim());
        return match && ordinal(match[1]) === target.item;
      });
      selected = items.length === 1 ? items : [];
    }
    if (selected.length) highlights.set(idx, selected);
  }
  return { lines, heading, version, effective, targets, highlights };
}
