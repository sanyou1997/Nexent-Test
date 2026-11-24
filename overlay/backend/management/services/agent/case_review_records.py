"""Source-bound case review records. Line numbers are assigned before any model call."""

import calendar
import re
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser

DATE = re.compile(
    r"(?:19|20)\d{2}\s*(?:年|[-/.])(?:\s*\d{1,2}\s*(?:月|[-/.])?(?:\s*\d{1,2}\s*日?)?)?"
)
MONEY = re.compile(r"([0-9][0-9,，]*(?:\.\d+)?)\s*元")
PAGE = re.compile(r"【(.+?) · PDF 第 (\d+) 页】")
ARTICLE = re.compile(r"第([一二三四五六七八九十百千万零〇两\d]+)条")
SPEC = re.compile(
    r"^法规定位：《(.+?)》[；;](.+?)[；;]第([一二三四五六七八九十百千万零〇两\d]+)条\s*$"
)
WS = re.compile(r"\s+")


def compact(text):
    return WS.sub("", str(text))


def number(text):
    if text.isdigit():
        return int(text)
    total = digit = 0
    for c in text:
        if c in "十百千万":
            total += (digit or 1) * {"十": 10, "百": 100, "千": 1000, "万": 10000}[c]
            digit = 0
        else:
            digit = "零一二三四五六七八九".index(
                c.replace("〇", "零").replace("两", "二")
            )
    return total + digit


class TableRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = []
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row:
            self.rows.append(self.row)


def source_lines(text):
    """Replace table markup with physical rows, preserving page and column provenance."""
    lines, transactions, warnings = {}, {}, []
    location = "提交文本"

    def add(body):
        line = len(lines) + 1
        lines[line] = (location, body)
        return line

    def emit_table(html):
        table = TableRows()
        table.feed(html)
        if not table.rows:
            warnings.append(f"{location} 表格未能还原，待核原页")
            return
        headers = table.rows[0]
        index = next(
            (i for i, h in enumerate(headers) if re.search("流水|交易编号|凭证号", h)),
            None,
        )
        for cells in table.rows:
            line = add(" ｜ ".join(cells))
            if index is None or cells is headers or len(cells) != len(headers):
                continue
            key = cells[index].strip()
            if not key:
                continue
            rec = dict(zip(headers, cells))
            when = next((v for h, v in rec.items() if "日期" in h or "时间" in h), "")
            amt = next((v for h, v in rec.items() if "金额" in h), "")
            remark = next(
                (v for h, v in rec.items() if re.search("摘要|用途|备注", h)), ""
            )
            transactions.setdefault((location.rsplit(" · PDF", 1)[0], key), []).append(
                (line, when, amt, remark)
            )

    for block in re.split(r"(<table\b[^>]*>.*?</table>)", text, flags=re.I | re.S):
        if re.match("<table", block, re.I):
            emit_table(block)
            continue
        for line in block.splitlines():
            line = line.strip()
            page = PAGE.fullmatch(line)
            if page:
                location = page[1] + " · PDF 第" + page[2] + "页"
            elif line:
                add(line)
                if any(
                    x in line for x in ("未纳入本轮", "未提取到文字", "本附件解析失败")
                ):
                    warnings.append(line)
    return lines, transactions, warnings


def bounds(when):
    fields = [int(x) for x in re.findall(r"\d+", when)]
    try:
        if len(fields) == 3:
            d = date(*fields)
            return d, d
        if len(fields) == 2:
            y, m = fields
            return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
        if len(fields) == 1 and 1900 <= fields[0] <= 2100:
            return date(fields[0], 1, 1), date(fields[0], 12, 31)
    except ValueError:
        pass
    return None


def fact_passages(batch):
    """Stable sentence IDs retain the original page and physical line across batches."""
    passages = {}
    for n, _, body in batch:
        for j, part in enumerate(re.findall(r"[^。！？；]+[。！？；]*|[。！？；]+", body), 1):
            if part.strip():
                passages[f"{n}.{j}"] = {"line": n, "quote": part.strip()}
    return passages


def expand_fact_refs(result, passages):
    for key in ("facts", "findings"):
        items = result.get(key, [])
        if not isinstance(items, list):
            raise ValueError("Expected a list of review records")
        result[key] = [item for item in items if isinstance(item, dict)]
        for item in result[key]:
            ids = item.get("refs")
            item["evidence"] = [passages[s] for s in ids if isinstance(s, str) and s in passages] if isinstance(ids, list) else []
            timing = item.get("time")
            if not isinstance(timing, dict):
                item["time"] = {}
                continue
            source = passages.get(str(timing.get("ref", "")))
            item["time"] = dict(source, date=timing.get("date", ""), basis=timing.get("basis", "")) if source else {}
    return result


def cited(rows, evidence):
    """Only accept a literal quotation at a source position in this run."""
    checked = []
    for item in evidence if isinstance(evidence, list) else []:
        if not isinstance(item, dict):
            continue
        n = item.get("line")
        quote = str(item.get("quote", "")).strip()
        if (
            type(n) is int
            and n in rows
            and len(compact(quote)) >= 4
            and compact(quote) in compact(rows[n][1])
        ):
            checked.append((n, quote))
    return list(dict.fromkeys(checked))


def facts_from_plan(lines, transactions, plan):
    facts = []
    for item in plan.get("facts", []):
        if not isinstance(item, dict):
            continue
        evidence = cited(lines, item.get("evidence"))
        if not evidence:
            continue
        timing = item.get("time") or {}
        time_ref = cited(lines, [timing]) if isinstance(timing, dict) else []
        when = str(timing.get("date", "")).strip() if time_ref else ""
        anchored = {n for n, _ in evidence}
        if not bounds(when) or not any(
            n in anchored and compact(when) in compact(q) for n, q in time_ref
        ):
            when = ""
            time_ref = []
        facts.append(
            dict(
                label=str(item.get("label", "待核行为")),
                when=when,
                evidence=evidence,
                time_ref=time_ref,
                certainty=str(item.get("certainty", "待核")),
                issue=str(item.get("issue", "")),
                terms=str(item.get("terms", "")),
                basis=timing.get("basis", "背景时间") if time_ref else "时间待核",
                transaction=item.get("transaction", ""),
            )
        )
    used = set()
    for (_, key), entries in transactions.items():
        row_ids = {x[0] for x in entries}
        # A narrative entry can describe this transfer (or a separate refund). Only
        # merge the original transfer if the model grounded it in a matching table row.
        match = next(
            (
                f
                for f in facts
                if f["transaction"] == key
                and any(n in row_ids for n, _ in f["evidence"])
            ),
            None,
        )
        if not match:
            match = dict(
                label=key,
                evidence=[],
                issue="流水性质及对价待核",
                terms="",
                certainty="待核",
                transaction=key,
            )
            facts.append(match)
        ds = {compact(e[1]) for e in entries}
        amounts = {compact(e[2]) for e in entries}
        conflicting = len(ds) != 1 or len(amounts) != 1
        n, when, amt, remark = entries[0]
        if not match["terms"]:
            match["terms"] = remark
        match.update(
            label=f"{key} · {amt} · {remark}",
            when=when if not conflicting else "",
            time_ref=[(n, lines[n][1])],
            basis="表内交易日" if not conflicting else "同号日期或金额冲突",
            entries=entries,
            conflicting=conflicting,
        )
        match["evidence"] = list(
            dict.fromkeys([(i, lines[i][1]) for i, *_ in entries] + match["evidence"])
        )
        match["amount_leads"] = []
        numeric = MONEY.fullmatch(amt.strip())
        if numeric:
            amount_value = Decimal(numeric[1].replace(",", "").replace("，", ""))
            for j, (_, text) in lines.items():
                if j in row_ids or "｜" in text or key in text:
                    continue
                for m in MONEY.finditer(text):
                    if Decimal(m[1].replace(",", "").replace("，", "")) == amount_value:
                        match["amount_leads"].append(
                            (j, text[max(0, m.start() - 20) : m.end() + 65])
                        )
                        break
                if len(match["amount_leads"]) >= 4:
                    break
        # Direct mentions in other pages are evidence pointers, regardless of model recall.
        for i, (loc, text) in lines.items():
            if i not in row_ids and re.search(
                r"(?<![\w-])" + re.escape(key) + r"(?![A-Za-z0-9-])", text, re.ASCII
            ):
                if not any(e[0] == i for e in match["evidence"]):
                    at = text.index(key)
                    match["evidence"].append((i, text[max(0, at - 36) : at + 75]))
        used.add(id(match))
    table_lines = {e[0] for entries in transactions.values() for e in entries}
    return [
        f
        for f in facts
        if id(f) in used
        or not (
            f.get("transaction") and any(n in table_lines for n, _ in f["evidence"])
        )
    ]


def parse_law(text):
    lines = text.splitlines()
    effective = next(
        (s.split("：", 1)[1].strip() for s in lines[:8] if s.startswith("施行日期：")),
        "",
    )
    if not effective:
        return []
    date.fromisoformat(effective)
    result = []
    for match in re.finditer(r"^法规定位：.*$", text, re.M):
        spec = SPEC.match(match.group().strip())
        if not spec:
            continue
        stop = text.find("\n法规定位：", match.end())
        body = text[match.end() : stop if stop >= 0 else len(text)].strip()
        head = ARTICLE.match(body)
        if head and number(head[1]) == number(spec[3]):
            result.append(
                dict(
                    title=spec[1],
                    version=spec[2],
                    effective=effective,
                    num=number(head[1]),
                    body=body,
                )
            )
    return result


def period_status(law, when, catalog):
    rng = bounds(when)
    if not rng:
        return "行为时间待核"
    dates = sorted({x["effective"] for x in catalog if x["title"] == law["title"]})
    start = date.fromisoformat(law["effective"])
    later = next((date.fromisoformat(d) for d in dates if d > law["effective"]), None)
    if rng[1] < start:
        return "该版本施行在行为之后，仅供衔接比较"
    if later and rng[0] >= later:
        return "已有更晚施行的库存版本，须复核衔接"
    if rng[0] < start or (later and rng[1] >= later):
        return "行为时段跨施行界限，待核具体日期"
    return "行为日期落在库存版本期间；衔接及效力须复核"


def direction_mismatch(report_line, article, cited_num):
    """Only classify an explicit evidentiary claim linked to this very citation."""
    for marker in ARTICLE.finditer(report_line):
        if number(marker[1]) != cited_num:
            continue
        claim = re.match(
            r"\s*(?:作为|用于认定|认定)([^。；，]{1,36}?)(?:的依据|的定性依据|行为)",
            report_line[marker.end() :],
        )
        if not claim:
            continue
        first = ARTICLE.sub("", article, count=1).lstrip().split("。")[0]
        if (
            "收受" in claim[1]
            and first.startswith("向")
            and "赠送" in first
            and "收受" not in first
        ):
            return "原引方向不符：报告用它说明收受行为，该款规制赠送方。收受方须另核相应条文和交付证据。"
        if "赠送" in claim[1] and first.startswith("收受") and "赠送" not in first:
            return "原引方向不符：报告用它说明赠送行为，该款规制收受方。赠送行为须另核相应条文和证据。"
    return ""


def counterpart_candidates(text, original, catalog):
    """Find a unique same-edition counterpart by operative verb and named object.

    This identifies a candidate for review only. Evidence of control is a separate question.
    """
    if not direction_mismatch(text, original["body"], original["num"]):
        return []
    context = next(
        (
            text[m.end() :].split("依据", 1)[0][:45]
            for m in ARTICLE.finditer(text)
            if number(m[1]) == original["num"]
        ),
        "",
    )
    objects = {
        "购物卡": "消费卡",
        "储值卡": "消费卡",
        "礼品卡": "消费卡",
        "消费卡": "消费卡",
        "礼金": "礼金",
        "礼品": "礼品",
        "有价证券": "有价证券",
        "股权": "股权",
    }
    terms = {v for k, v in objects.items() if k in context}
    if not terms:
        return []
    result = []
    for law in catalog:
        if (law["title"], law["version"]) != (original["title"], original["version"]):
            continue
        first = ARTICLE.sub("", law["body"], count=1).lstrip().split("。")[0]
        receives = "收受" in context and first.startswith("收受")
        gives = "赠送" in context and first.startswith("向") and "赠送" in first
        if (receives or gives) and all(term in first for term in terms):
            result.append(law)
    return result if len(result) == 1 else []


def transaction_totals(transactions):
    raw = Decimal(0)
    unique = Decimal(0)
    count = 0
    for entries in transactions.values():
        vals = []
        for _, _, amt, _ in entries:
            m = MONEY.fullmatch(amt.strip())
            if not m:
                return None
            vals.append(Decimal(m[1].replace(",", "").replace("，", "")))
        if len(set(vals)) != 1 or len({compact(x[1]) for x in entries}) != 1:
            return None
        raw += sum(vals)
        unique += vals[0]
        count += len(vals)
    return (count, raw, len(transactions), unique) if transactions else None
