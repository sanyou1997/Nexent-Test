"""历史报告结构化识别引擎（离线优先，LLM 补空）。

目标：把 Word/PDF 历史报告批量导入后**自动识别内容建档**，产出可用于
「类案参考」与「多维统计」的结构化字段。

设计原则（与项目既有纪律一致）：
1. **规则优先、LLM 补空**：确定性规则抽取的字段置信度为 1.0，LLM 补齐的字段
   置信度打折（0.7），规则命中不了的留空并记入 missing_fields —— 绝不编造，
   缺失必须可见（这是"数据全了才有分析"的底线）。
2. **离线可用**：规则链完全不依赖大模型；本地小模型不可用时自动退化为纯规则，
   结果依然可建档（仅标记待补录）。
3. **复用既有能力**：PDF 走 app.rag.pdf_extract（文本层抽取 + 扫描页 OCR 兜底），
   Word 走 python-docx，检索向量走 app.llm.embeddings。
"""
from __future__ import annotations

import io
import re
from datetime import datetime

# ---------------------------------------------------------------- 词表定义

REPORT_TYPES = [
    "审计报告", "专项审计报告", "经济责任审计报告", "审计调查",
    "巡视报告", "巡察报告", "专项检查报告", "延伸检查报告",
    "审查调查报告", "核查报告", "评估报告", "其他",
]

AUDIT_TYPES = [
    "经济责任审计", "专项审计", "日常审计", "审计调查",
    "巡视巡察", "专项检查", "延伸检查", "other",
]

PROBLEM_CATEGORIES = [
    "三公经费", "公务支出", "津贴补贴", "违规发放", "资金管理",
    "工程建设", "招投标", "国有资产", "惠农补贴", "低保救助",
    "违规借贷", "吃喝送礼", "选人用人", "财经纪律", "其他",
]

SEVERITIES = ["一般", "重要", "重大"]

# 字段权重（用于置信度计算）：关键字段权重高，缺失影响大
FIELD_WEIGHTS = {
    "report_type": 3.0,
    "report_date": 3.0,
    "region": 3.0,
    "audited_org": 3.0,
    "findings": 3.0,
    "conclusion": 2.0,
    "problem_category": 2.0,
    "audit_type": 2.0,
    "amount": 1.0,
    "report_no": 1.0,
    "subject": 1.0,
}
LLM_CONFIDENCE = 0.7  # LLM 补齐字段的置信折扣

# 地区口径统一收敛到测试市 10 个区划，见 app/regions.py（不再维护全国省市词表）

# 中文数字（年份/金额单位用）
_CN_NUM = {
    "〇": 0, "零": 0, "○": 0, "O": 0,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(s: str) -> int | None:
    """中文数字转整数（支持 二〇二四 / 二十四）。"""
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, number = 0, 0, 0
    for ch in s:
        if ch in _CN_NUM:
            number = _CN_NUM[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            section += (number or 1) * unit
            number = 0
        else:
            return None
    total = section + number
    return total or None


# ---------------------------------------------------------------- 文本抽取

def extract_text(data: bytes, filename: str) -> dict:
    """从 Word/PDF 抽取纯文本。

    返回 {text, page_count, engine, note}；失败抛 ValueError（错误信息可直接展示）。
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        from app.rag.pdf_extract import extract_pdf
        res = extract_pdf(data, filename, return_images=False)
        if res.get("encrypted"):
            raise ValueError("PDF 已加密，请先解密后再导入")
        text = (res.get("text") or "").strip()
        if not text:
            raise ValueError("PDF 未识别到文字（可能是空白件或纯图片且 OCR 未取到文本）")
        return {
            "text": text,
            "page_count": len(res.get("pages") or []),
            "engine": res.get("engine", ""),
            "note": res.get("note", ""),
        }

    if name.endswith(".docx"):
        try:
            from docx import Document as Docx
        except Exception as e:  # pragma: no cover
            raise ValueError(f"Word 解析组件不可用：{e}")
        try:
            doc = Docx(io.BytesIO(data))
        except Exception as e:
            raise ValueError(f"Word 文件无法打开（可能已损坏或为旧版 .doc）：{e}")
        parts: list[str] = []
        for p in doc.paragraphs:
            t = (p.text or "").strip()
            if t:
                parts.append(t)
        for ti, table in enumerate(doc.tables, 1):
            rows = []
            for row in table.rows:
                cells = [(c.text or "").strip() for c in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                parts.append(f"[表{ti}]")
                parts.extend(rows)
        text = "\n".join(parts).strip()
        if not text:
            raise ValueError("Word 文档无有效文字内容")
        return {"text": text, "page_count": 0, "engine": "python-docx 文本抽取（本机·离线）",
                "note": f"段落 {len(doc.paragraphs)} 段 / 表格 {len(doc.tables)} 个"}

    if name.endswith(".txt"):
        for enc in ("utf-8", "gbk", "gb18030"):
            try:
                text = data.decode(enc).strip()
                if text:
                    return {"text": text, "page_count": 0, "engine": f"纯文本（{enc}）", "note": ""}
            except Exception:
                continue
        raise ValueError("文本文件编码无法识别")

    raise ValueError("仅支持 .docx / .pdf / .txt 格式（.doc 旧格式请先另存为 .docx）")


# ---------------------------------------------------------------- 规则抽取

_RE_NO = re.compile(
    r"([一-龥]{0,8}(?:字|号|函|〔|\[)?\s*[〔\[（(]\s*(?P<y>[12]\d{3})\s*[〕\]）)]\s*(?P<n>\d{1,4})\s*号)")
_RE_DATE_CN = re.compile(r"(?P<y>[〇零○O一二三四五六七八九]{4})\s*年\s*(?P<m>[〇零一二三四五六七八九十]{1,3})\s*月\s*(?P<d>[〇零一二三四五六七八九十]{1,3})\s*日?")
_RE_DATE_NUM = re.compile(r"(?P<y>(?:19|20)\d{2})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日?")
_RE_AMOUNT_WAN = re.compile(r"(?P<v>[\d,]+(?:\.\d+)?)\s*万\s*元")
_RE_AMOUNT_YI = re.compile(r"(?P<v>[\d,]+(?:\.\d+)?)\s*亿\s*元")
_RE_AMOUNT_YUAN = re.compile(r"(?P<v>[\d,]+(?:\.\d+)?)\s*元")
_RE_ARTICLE = re.compile(r"《(?P<law>[^《》]{2,40})》\s*第\s*(?P<no>[〇零一二三四五六七八九十百千\d]{1,8})\s*条")
_RE_ORG = re.compile(r"(?:对|关于)\s*([一-龥（）()]{4,40}?)\s*(?:进行|开展|实施|的)")
_RE_ORG_LABEL = re.compile(r"(?:被审计单位|被检查单位|被巡察单位|审计对象|被审计对象|受检单位)\s*[:：]?\s*([一-龥（）()]{2,40})")
# 标题式被审计单位：「关于XXX的审计报告」/「对XXX的审计」
_RE_ORG_TITLE = re.compile(r"^关于\s*([一-龥0-9（）()]{4,40}?)\s*(?:的|年度|期间|资金|项目)?\s*(?:专项|经济责任|工程|预算|资金)?\s*(?:审计|检查|巡察|调查)")
_RE_SUBJECT = re.compile(r"(?:被审计对象|被查对象|当事人|被反映人|责任人)\s*[:：]?\s*([一-龥（）()]{2,30})")
# 落款行（机关署名 + 日期）：用于截断段落
_RE_SIGN = re.compile(r"^[一-龥]{2,20}(?:局|厅|委|办|室|中心|处|科|院|部|署|组|队|会|政府|纪监|监委|审计)[一-龥]{0,10}\s*$")

_FINDING_HEADS = [
    "主要问题", "存在的问题", "主要存在以下问题", "发现的主要问题", "查出的主要问题",
    "主要违规问题", "主要问题及建议", "问题及建议", "审计发现",
]
# 纯小标题词（这些词构成的短行是章节名，不是问题条目）
_SECTION_HEAD_WORDS = {
    "主要问题", "存在的问题", "主要存在以下问题", "发现的主要问题", "查出的主要问题",
    "主要违规问题", "问题及建议", "审计发现", "处理意见", "处理建议", "整改建议",
    "审计建议", "处理决定", "整改要求", "建议", "结论", "主要建议", "基本情况",
    "主要事实", "问题", "意见", "主要情况", "存在的主要问题", "主要违规违纪问题",
}


def _is_section_heading(text: str) -> bool:
    """判断短文本是否为章节小标题（如「主要问题」「处理意见」）。"""
    t = re.sub(r"^[一二三四五六七八九十]{1,3}、|^（[一二三四五六七八九十]{1,3}）|^\d{1,2}[.、]|\(\d{1,2}\)|（\d{1,2}）",
               "", (text or "").strip()).strip()
    if not t:
        return True
    return t in _SECTION_HEAD_WORDS
_CONCLUSION_HEADS = [
    "处理意见", "处理建议", "整改建议", "审计建议", "处理决定", "整改要求",
    "建议", "结论", "主要建议",
]

_PROBLEM_KEYWORDS = {
    "三公经费": ["三公经费", "公务接待", "公务用车", "公务出国"],
    "公务支出": ["公务支出", "会议费", "培训费", "差旅费", "津补贴发放"],
    "津贴补贴": ["津贴补贴", "补贴发放", "绩效工资", "退休金", "最低工资"],
    "违规发放": ["违规发放", "超发", "虚发", "挤占挪用", "截留", "挪用"],
    "资金管理": ["资金管理", "专项资金", "资金闲置", "账户管理", "资金沉淀", "公款私存"],
    "工程建设": ["工程建设", "工程款", "项目施工", "招投标", "工程结算", "变更签证", "工程项目"],
    "招投标": ["招投标", "招标", "投标", "围标", "串标", "评标", "政府采购", "采购程序", "供应商"],
    "国有资产": ["国有资产", "资产处置", "闲置资产", "出租出借", "房产"],
    "惠农补贴": ["惠农补贴", "涉农补贴", "农业补贴", "耕地地力", "种粮补贴", "退耕还林"],
    "低保救助": ["低保", "低保金", "救助资金", "临时救助", "低保审核"],
    "违规借贷": ["违规借贷", "违规借款", "公款私借", "拆借资金", "违规担保"],
    "吃喝送礼": ["吃喝送礼", "违规收受", "礼品礼金", "红包", "宴请", "接受宴请"],
    "选人用人": ["选人用人", "干部选拔", "提拔任用", "违规兼职", "亲属回避"],
    "财经纪律": ["财经纪律", "财务制度", "会计核算", "虚列支出", "坐支", "账外账"],
}

_AUDIT_TYPE_KEYWORDS = {
    "经济责任审计": ["经济责任审计", "领导干部经济责任", "离任审计"],
    "专项审计": ["专项审计", "专项资金审计", "专项审计报告"],
    "日常审计": ["日常审计", "常规审计", "年度审计"],
    "审计调查": ["审计调查", "专项调查"],
    "巡视巡察": ["巡视", "巡察"],
    "专项检查": ["专项检查", "专项检查报告", "监督检查"],
    "延伸检查": ["延伸检查", "下沉一级"],
}

_SEVERITY_KEYWORDS = {
    "重大": ["重大违纪", "严重违纪违法", "性质严重", "重大问题", "移送司法机关", "追究刑事责任"],
    "重要": ["重要问题", "比较严重", "违反中央八项规定精神", "情节较重", "违规发放", "虚报"],
}

_TITLE_NOISE = re.compile(r"^[（(【\[]|[）)】\]]|附件\s*\d*[:：]?\s*$")


def _clean_title(line: str) -> str:
    t = (line or "").strip()
    t = _TITLE_NOISE.sub("", t)
    return t.strip(" 　:：-—_·")


def _extract_date(text: str) -> tuple[str, int]:
    """返回 (YYYY-MM-DD, year)。取文中最后一个日期（落款时间通常在末尾）。"""
    cands: list[tuple[int, str]] = []
    for m in _RE_DATE_NUM.finditer(text):
        y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d") or 1)
        if not (1 <= mo <= 12) or not (1 <= d <= 31):
            continue
        cands.append((m.start(), f"{y:04d}-{mo:02d}-{d:02d}"))
    for m in _RE_DATE_CN.finditer(text):
        y = _cn_to_int(m.group("y"))
        mo = _cn_to_int(m.group("m"))
        d = _cn_to_int(m.group("d")) or 1
        if not y or not mo or not (1 <= mo <= 12) or not (1 <= d <= 31):
            continue
        cands.append((m.start(), f"{y:04d}-{mo:02d}-{d:02d}"))
    if not cands:
        return "", 0
    cands.sort(key=lambda x: x[0])
    ds = cands[-1][1]
    return ds, int(ds[:4])


def _extract_amount(text: str) -> float:
    """涉及金额（统一换算为万元）。取最大一笔，避免小数目噪声。"""
    vals: list[float] = []
    for m in _RE_AMOUNT_YI.finditer(text):
        try:
            vals.append(float(m.group("v").replace(",", "")) * 10000)
        except Exception:
            pass
    for m in _RE_AMOUNT_WAN.finditer(text):
        try:
            vals.append(float(m.group("v").replace(",", "")))
        except Exception:
            pass
    for m in _RE_AMOUNT_YUAN.finditer(text):
        try:
            v = float(m.group("v").replace(",", ""))
            if v >= 10000:  # 万元级以上的"元"才计入
                vals.append(v / 10000)
        except Exception:
            pass
    return round(max(vals), 2) if vals else 0.0


def _extract_region(text: str) -> str:
    """地区识别：收敛到测试市 10 个区划口径（见 app/regions.py）。

    不再按全国省市通用规则匹配——本系统历史报告来源即本地，
    通用规则会把"广州""天河"等外地地名误判进来，破坏地区统计口径。
    """
    from app.regions import detect_region
    return detect_region(text)


def _is_sign_line(line: str) -> bool:
    """判断是否为落款行（机关署名或落款日期），用于截断正文段落。"""
    if not line or len(line) > 40:
        return False
    if _RE_DATE_NUM.search(line) or _RE_DATE_CN.search(line):
        return True
    if _RE_SIGN.match(line):
        return True
    return False


def _extract_section(text: str, heads: list[str], max_chars: int = 1200) -> str:
    """按小标题切段，抽取问题发现/处理意见段落（遇落款行即截断，避免串入署名日期）。"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        for h in heads:
            if h in line and len(line) <= len(h) + 12:
                chunk: list[str] = []
                for j in range(i + 1, min(i + 60, len(lines))):
                    nxt = lines[j]
                    if any(hh in nxt and len(nxt) <= len(hh) + 12 for hh in _FINDING_HEADS + _CONCLUSION_HEADS):
                        break
                    if _is_sign_line(nxt):
                        break
                    chunk.append(nxt)
                    if sum(len(c) for c in chunk) > max_chars:
                        break
                if chunk:
                    return "\n".join(chunk)[:max_chars]
    return ""


def _pick_type(text: str, title: str) -> str:
    head = (title or "") + "\n" + text[:1500]
    best, best_pos = "", 10**6
    for t in REPORT_TYPES:
        pos = head.find(t)
        if pos >= 0 and pos < best_pos:
            best, best_pos = t, pos
    return best


def _pick_by_keywords(text: str, table: dict[str, list[str]], default: str) -> str:
    best, best_n = default, 0
    for key, kws in table.items():
        n = sum(text.count(k) for k in kws)
        if n > best_n:
            best, best_n = key, n
    return best


def split_findings(text: str) -> list[dict]:
    """把报告正文拆成「问题条目」列表。

    识别两类结构：
    - 中文序号：「一、」「二、」「（一）」「1.」
    - 显式小标题：一、/（一）/ 1. 开头的行
    每条尽量抽出：序号、标题、正文、金额、问题类型、严重程度、引用法规。
    拆不出就返回空列表（不硬造）。
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    # 候选切点：行首是序号
    head_re = re.compile(
        r"^(?P<seq>[一二三四五六七八九十]{1,3}、|（[一二三四五六七八九十]{1,3}）"
        r"|\d{1,2}[.、]|\(\d{1,2}\)|（\d{1,2}）)\s*(?P<rest>.*)$")
    marks: list[tuple[int, str, str]] = []
    for i, l in enumerate(lines):
        m = head_re.match(l)
        if m:
            rest = (m.group("rest") or "").strip()
            # 序号行本身太长的，多半是正文而非小标题——仍然作为切点（内容并入本条）
            marks.append((i, m.group("seq"), rest))
    if len(marks) < 2:
        return []

    out: list[dict] = []
    for idx, (li, seq, first) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(lines)
        body_parts = ([first] if first else []) + lines[li + 1:end]
        body = "\n".join(p for p in body_parts if p).strip()
        if not body:
            continue
        # 纯小标题行（如「一、主要问题」「二、处理意见」）不是问题条目，跳过
        if _is_section_heading(body):
            continue
        # body 以小标题词开头（如「处理意见\n责令退还…」）→ 属结论段，不是问题条目
        head_word = re.split(r"[\n。；;]", body, 1)[0].strip()
        if head_word in _SECTION_HEAD_WORDS and len(head_word) <= 12:
            continue
        if len(body) > 4000:
            body = body[:4000]
        amt = _extract_amount(body)
        ptype = _pick_by_keywords(body, _PROBLEM_KEYWORDS, "")
        sev = _pick_by_keywords(body, _SEVERITY_KEYWORDS, "")
        refs = []
        seen = set()
        for m in _RE_ARTICLE.finditer(body):
            key = (m.group("law").strip(), m.group("no").strip())
            if key in seen:
                continue
            seen.add(key)
            refs.append({"law": key[0], "article_no": f"第{key[1]}条"})
            if len(refs) >= 10:
                break
        # 条目标题：取序号后第一句的短前缀（≤40字且不含句号时优先）
        title = ""
        m2 = re.match(r"^(?P<t>[^。；;，,]{4,40})[。；;，,]", body)
        if m2:
            title = m2.group("t").strip()
        out.append({
            "seq": seq, "title": title, "content": body,
            "amount": amt, "problem_type": ptype, "severity": sev,
            "legal_refs": refs,
        })
    return out


def extract_by_rules(text: str, filename: str = "") -> dict:
    """纯规则抽取（确定性、离线）。返回 {field: value}，未命中的不出现。"""
    fields: dict = {}
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # 标题：首个较像标题的短行；否则用文件名
    title = ""
    for l in lines[:12]:
        if 6 <= len(l) <= 60 and not re.match(r"^[\d\s、.．·—-]+$", l):
            if any(t in l for t in ("报告", "意见", "方案", "通报", "决定", "说明", "记录")):
                title = _clean_title(l)
                break
    if not title:
        for l in lines[:6]:
            if 6 <= len(l) <= 60:
                title = _clean_title(l)
                break
    if not title:
        title = (filename or "").rsplit(".", 1)[0]
    fields["title"] = title

    # 报告文号
    m = _RE_NO.search(text[:3000])
    if m:
        fields["report_no"] = re.sub(r"\s+", "", m.group(0))

    # 日期
    date_str, year = _extract_date(text)
    if date_str:
        fields["report_date"] = date_str
        fields["year"] = year

    # 地区
    region = _extract_region(text)
    if region:
        fields["region"] = region

    # 被审计单位
    org = ""
    m = _RE_ORG_LABEL.search(text[:4000])
    if m:
        org = m.group(1)
    if not org:
        m = _RE_ORG_TITLE.search(title)
        if m:
            org = m.group(1)
    if not org:
        m = _RE_ORG.search(text[:4000])
        if m:
            org = m.group(1)
    # 截断：「XX街道2025年度三公经费使用情况」→ 单位名到年度/期间/资金之前
    org = re.split(r"\d{4}\s*年|年度|期间|专项资金|资金使用|预算|收支|项目|情况$", org)[0]
    org = re.sub(r"(采购情况|使用情况|收支情况|工作情况|情况)$", "", org)
    org = re.sub(r"^(进行|开展|实施)", "", org).strip()
    if org and 2 <= len(org) <= 40:
        fields["audited_org"] = org

    # 审计类型 / 问题类别 / 严重程度
    at = _pick_by_keywords(text, _AUDIT_TYPE_KEYWORDS, "")
    if at:
        fields["audit_type"] = at
    pc = _pick_by_keywords(text, _PROBLEM_KEYWORDS, "")
    if pc:
        fields["problem_category"] = pc
    sev = _pick_by_keywords(text, _SEVERITY_KEYWORDS, "")
    if sev:
        fields["severity"] = sev
    rt = _pick_type(text, title)
    if rt:
        fields["report_type"] = rt

    # 金额
    amt = _extract_amount(text)
    if amt:
        fields["amount"] = amt

    # 对象
    m = _RE_SUBJECT.search(text[:5000])
    if m:
        fields["subject"] = m.group(1)

    # 问题发现 / 处理意见
    f = _extract_section(text, _FINDING_HEADS)
    if f:
        fields["findings"] = f
    c = _extract_section(text, _CONCLUSION_HEADS)
    if c:
        fields["conclusion"] = c

    # 引用法规条款（去重，最多 30 条）
    refs, seen = [], set()
    for m in _RE_ARTICLE.finditer(text):
        law = m.group("law").strip()
        no = m.group("no").strip()
        key = (law, no)
        if key in seen:
            continue
        seen.add(key)
        refs.append({"law": law, "article_no": f"第{no}条"})
        if len(refs) >= 30:
            break
    if refs:
        fields["legal_refs"] = refs

    return fields


# ---------------------------------------------------------------- LLM 补空

_LLM_SYS = (
    "你是纪检监察/审计文书结构化助手。请从给定的报告全文中抽取结构化字段，"
    "只输出 JSON，不要任何解释文字。字段说明："
    '{"report_type":"报告类型(如 审计报告/专项审计报告/巡视报告/审查调查报告)",'
    '"report_date":"报告日期 YYYY-MM-DD",'
    '"region":"地区(省+市)",'
    '"audited_org":"被审计或被检查单位",'
    '"audit_type":"审计类型(经济责任审计/专项审计/日常审计/审计调查/巡视巡察/专项检查/延伸检查)",'
    '"problem_category":"问题类别(三公经费/公务支出/津贴补贴/违规发放/资金管理/工程建设/招投标/'
    '国有资产/惠农补贴/低保救助/违规借贷/吃喝送礼/选人用人/财经纪律/其他)",'
    '"severity":"严重程度(一般/重要/重大)",'
    '"amount":涉及金额(万元,数字),'
    '"subject":"被审计对象或当事人",'
    '"findings":"主要问题发现的简要概括(200字内)",'
    '"conclusion":"处理意见或审计建议概括(200字内)",'
    '"tags":["关键词标签"]}。'
    "无法确定的字段请填空字符串或空数组，禁止编造。"
)

_LLM_USER = """报告标题：{title}

报告全文：
{text}
"""


def fill_by_llm(text: str, title: str, missing: list[str], user_id=None) -> dict:
    """用本地/云端大模型补齐规则未命中的字段。失败返回 {}（静默降级为纯规则）。"""
    if not missing or len(text) < 40:
        return {}
    try:
        from app.llm import provider
        head = text[:6000]
        raw = provider.chat_json(
            [{"role": "system", "content": _LLM_SYS},
             {"role": "user", "content": _LLM_USER.format(title=title, text=head)}],
            scene="extract", user_id=user_id, timeout=240,
        )
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for k in ("report_type", "report_date", "region", "audited_org", "audit_type",
              "problem_category", "severity", "amount", "subject", "findings",
              "conclusion", "tags"):
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        elif isinstance(v, (int, float)):
            v = float(v) if k == "amount" else v
        elif isinstance(v, list):
            v = [str(x).strip() for x in v if str(x).strip()][:10]
        else:
            continue
        out[k] = v
    return out


# ---------------------------------------------------------------- 汇总

def _norm_date(v) -> str:
    s = str(v or "").strip()
    m = re.search(r"(\d{4})\D{0,2}(\d{1,2})\D{0,2}(\d{1,2})", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    m = re.search(r"(\d{4})\D{0,2}(\d{1,2})", s)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return f"{y:04d}-{mo:02d}-01"
    return s[:10] if len(s) >= 4 else ""


def build_record(text: str, filename: str, use_llm: bool = True, user_id=None) -> dict:
    """完整识别流程：规则 → 计算缺失 → LLM 补空 → 置信度与缺失清单。

    返回 {fields, confidence, missing, engine_note}
    """
    fields = extract_by_rules(text, filename)

    # 规则命中即确认；其余进缺失清单
    missing = [k for k in FIELD_WEIGHTS if not fields.get(k)]
    llm_used = False
    if use_llm and missing:
        filled = fill_by_llm(text, fields.get("title", filename), missing, user_id=user_id)
        for k, v in filled.items():
            if k in missing and v not in (None, "", [], {}):
                fields[k] = v
                llm_used = True
        missing = [k for k in FIELD_WEIGHTS if not fields.get(k)]

    # 归一
    if fields.get("report_date"):
        fields["report_date"] = _norm_date(fields["report_date"])
        if not fields.get("year") and len(fields["report_date"]) >= 4:
            try:
                fields["year"] = int(fields["report_date"][:4])
            except Exception:
                pass
    if fields.get("amount") is not None:
        try:
            fields["amount"] = round(float(fields["amount"]), 2)
        except Exception:
            fields["amount"] = 0.0
    if isinstance(fields.get("tags"), str):
        fields["tags"] = [t for t in re.split(r"[,，;；\s]+", fields["tags"]) if t][:10]

    # 置信度：加权命中率（LLM 补齐按折扣计）
    total_w = sum(FIELD_WEIGHTS.values())
    got = 0.0
    for k, w in FIELD_WEIGHTS.items():
        if fields.get(k):
            got += w * (LLM_CONFIDENCE if llm_used and k in missing else 1.0)
    confidence = round(got / total_w, 3) if total_w else 0.0

    note = "规则识别" + ("+大模型补全" if llm_used else "（未启用大模型）")
    return {"fields": fields, "confidence": confidence, "missing": missing, "engine_note": note}
