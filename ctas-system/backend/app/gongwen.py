"""公文格式引擎（GB/T 9704-2012 党政机关公文格式简化版）。

提供两个能力：
1. format_document：纯文本文书的公文规范化（离线规则，无需 LLM）
2. to_docx：导出标准公文格式 Word（标题二号小标宋居中、正文三号仿宋、
   首行缩进 2 字符、固定行距 28.9 磅、落款与成文日期右对齐）
"""
import io
import re

DATE_RE = re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日$")
H1_RE = re.compile(r"^[一二三四五六七八九十]+、")
H2_RE = re.compile(r"^[（(][一二三四五六七八九十]+[)）]")
H3_RE = re.compile(r"^\d+[.．]")

TITLE_BLACK = ("关于",)  # 常见公文标题起始词（仅用于标题识别加权）

# GB/T 9704-2012 党政机关公文格式（简化版）标准常数 —— 生成与校验共用，避免两套标准打架
GONGWEN_STD = {
    "page_top_cm": 3.7, "page_bottom_cm": 3.5, "page_left_cm": 2.8, "page_right_cm": 2.6,
    "title_size_pt": 22, "title_fonts": ("方正小标宋简体", "方正小标宋", "小标宋简体", "小标宋"),
    "body_size_pt": 16,  "body_fonts": ("仿宋_GB2312", "仿宋", "方正仿宋"),
    "h1_size_pt": 16,    "h1_fonts": ("黑体", "方正黑体"),
    "h2_size_pt": 16,    "h2_fonts": ("楷体_GB2312", "楷体", "方正楷体"),
    "line_spacing_pt": 28.9, "first_line_indent_chars": 2,
}


def _is_date(s: str) -> bool:
    return bool(DATE_RE.match(s.strip()))


def split_structure(content: str):
    """把文书正文拆成 (标题行列表, 落款机关, 成文日期, 正文段落列表)。"""
    lines = [l.strip() for l in content.replace("\r\n", "\n").split("\n")]
    # 去掉首尾空行
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()

    # 标题：开头连续的短行（非层级编号开头）；回行仅当上一行以"关于"等起始
    title: list[str] = []
    i = 0
    while i < len(lines):
        l = lines[i]
        if not l:
            break
        if H1_RE.match(l) or H2_RE.match(l) or H3_RE.match(l):
            break
        if len(l) > 44:
            break
        if title and not title[-1].startswith(TITLE_BLACK) and len("".join(title)) > 18:
            break
        title.append(l)
        i += 1

    body = lines[i:]
    # 从尾部提取落款（日期 + 机关名）
    org, date = "", ""
    if body and _is_date(body[-1]):
        date = body.pop()
        while body and not body[-1]:
            body.pop()
        if body and len(body[-1]) <= 30 and not H1_RE.match(body[-1]) \
                and not body[-1].endswith(("。", "；", "！", "？", "：")):
            org = body.pop()
    return title, org, date, body


def format_document(content: str) -> str:
    """离线公文规范化：结构分行、空行分节、落款右置提示。"""
    title, org, date, body = split_structure(content)
    out: list[str] = []
    if title:
        out.append("\n".join(title))
    for l in body:
        s = l.strip()
        if not s:
            out.append("")
            continue
        # 层级标题保持独立行；普通段落合并内部换行
        out.append(s)
    if org or date:
        out.append("")
        if org:
            out.append(org)
        if date:
            out.append(date)
    text = "\n".join(out)
    # 合并 3+ 连续空行为 1 个（公文分节）
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def to_docx(content: str, title_font=None, body_font=None, h_font=None) -> bytes:
    """导出公文格式 docx（GB/T 9704 简化版，参数全部取自 GONGWEN_STD，与校验共用一套标准）。"""
    from docx import Document
    from docx.shared import Pt, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING

    S = GONGWEN_STD
    title_font = title_font or S["title_fonts"][0]
    body_font = body_font or S["body_fonts"][0]
    h_font = h_font or S["h1_fonts"][0]

    def styled(p, text, font, size, bold=False, align=None, indent_chars=0.0,
               right_indent_cm=0.0, line_pt=None):
        p.alignment = align if align is not None else WD_ALIGN_PARAGRAPH.JUSTIFY
        if indent_chars:
            p.paragraph_format.first_line_indent = Pt(size * indent_chars)
        if right_indent_cm:
            p.paragraph_format.right_indent = Cm(right_indent_cm)
        if line_pt:
            p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
            p.paragraph_format.line_spacing = Pt(line_pt)
        r = p.add_run(text)
        r.font.name = "Times New Roman"
        r._element.rPr.rFonts.set(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}eastAsia", font)
        r.font.size = Pt(size)
        r.font.bold = bold
        return p

    doc = Document()
    # A4 + 公文页边距：上3.7 下3.5 左2.8 右2.6（cm）
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(S["page_top_cm"]), Cm(S["page_bottom_cm"])
    sec.left_margin, sec.right_margin = Cm(S["page_left_cm"]), Cm(S["page_right_cm"])

    title, org, date, body = split_structure(content)

    # 标题：二号（22pt）小标宋居中
    if title:
        for t in title:
            styled(doc.add_paragraph(), t, title_font, S["title_size_pt"],
                   align=WD_ALIGN_PARAGRAPH.CENTER)
        doc.add_paragraph()  # 标题后空行

    for l in body:
        s = l.strip()
        if not s:
            doc.add_paragraph()
            continue
        if H1_RE.match(s):
            styled(doc.add_paragraph(), s, h_font, S["h1_size_pt"], indent_chars=2,
                   line_pt=S["line_spacing_pt"])
        elif H2_RE.match(s):
            styled(doc.add_paragraph(), s, S["h2_fonts"][0], S["h2_size_pt"], bold=True,
                   indent_chars=2, line_pt=S["line_spacing_pt"])
        else:
            styled(doc.add_paragraph(), s, body_font, S["body_size_pt"], indent_chars=2,
                   line_pt=S["line_spacing_pt"])

    if org or date:
        doc.add_paragraph()
        if org:
            styled(doc.add_paragraph(), org, body_font, S["body_size_pt"],
                   align=WD_ALIGN_PARAGRAPH.RIGHT, right_indent_cm=0.74, line_pt=S["line_spacing_pt"])
        if date:
            styled(doc.add_paragraph(), date, body_font, S["body_size_pt"],
                   align=WD_ALIGN_PARAGRAPH.RIGHT, right_indent_cm=1.6, line_pt=S["line_spacing_pt"])

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
           "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_seq(text: str, pat) -> list[int]:
    """提取某层级编号的中文数字序列（如 '一、' -> [1]）。"""
    seq = []
    for m in pat.finditer(text):
        n = _CN_NUM.get(m.group(1))
        if n:
            seq.append(n)
    return seq


def _seq_continuous(seq: list[int]) -> bool:
    """检查序列是否从 1 开始且连续（1,2,3...）。"""
    if not seq:
        return True
    if seq[0] != 1:
        return False
    return all(seq[i] == seq[i - 1] + 1 for i in range(1, len(seq)))


def check_document(content: str) -> dict:
    """公文排版规范性检查（离线规则，GB/T 9704 简化版）。

    返回：{'score': int(0-100), 'level': str, 'issues': [...], 'normalized': str, 'changed': bool}
    - level: 合格 / 基本合格 / 待整改
    - issues: [{severity:'error'|'warning'|'info', rule, message, line?}]
    - normalized: 调用 format_document 后的规范化文本（供一键应用）
    """
    title, org, date, body = split_structure(content)
    issues: list[dict] = []
    lines = content.replace("\r\n", "\n").split("\n")

    # 1) 标题
    if not title:
        issues.append({"severity": "error", "rule": "title",
                       "message": "缺少公文标题（标题应置于正文前、居中、用二号小标宋）"})
    else:
        if any(len(t) > 44 for t in title):
            issues.append({"severity": "warning", "rule": "title",
                           "message": "标题单行过长（>44 字），建议分行或精简"})
        if not title[0].startswith("关于"):
            issues.append({"severity": "info", "rule": "title",
                           "message": "标题未以『关于』起首，非典型公文标题结构（如：关于××的××）"})

    # 2) 占位符 / 待补充 残留（结构缺失，必须修）
    for i, l in enumerate(lines, 1):
        s = l.strip()
        if "{{" in l or "}}" in l:
            issues.append({"severity": "error", "rule": "placeholder", "line": i,
                           "message": f"第{i}行存在未替换的占位符：{s[:40]}"})
        if "〔待补充〕" in l:
            issues.append({"severity": "error", "rule": "placeholder", "line": i,
                           "message": f"第{i}行含未填写项〔待补充〕，正式文书不得留白"})

    # 3) 层级编号混用 / 跳级
    h1 = [(i, l.strip()) for i, l in enumerate(lines, 1) if H1_RE.match(l.strip())]
    h2 = [(i, l.strip()) for i, l in enumerate(lines, 1) if H2_RE.match(l.strip())]
    h3 = [(i, l.strip()) for i, l in enumerate(lines, 1) if H3_RE.match(l.strip())]
    if h1:
        if not _seq_continuous(_cn_seq(content, H1_RE)):
            issues.append({"severity": "warning", "rule": "hierarchy",
                           "message": "一级标题编号（一、二、…）未从『一、』起或存在跳号"})
    if h2 and h1 and not _seq_continuous(_cn_seq(content, H2_RE)):
        issues.append({"severity": "warning", "rule": "hierarchy",
                       "message": "二级标题编号（（一）（二）…）存在跳号或不连续"})
    if h3 and not re.search(r"^\d+[.．]", content.strip().split("\n")[-1]) and h3:
        issues.append({"severity": "info", "rule": "hierarchy",
                       "message": f"检测到三级编号（共{len(h3)}处），正文宜统一层级规范"})

    # 4) 落款 / 成文日期
    if not date:
        issues.append({"severity": "warning", "rule": "sign",
                       "message": "缺少成文日期（落款应含机关名称与成文日期，右对齐）"})
    else:
        if not DATE_RE.match(date.strip()):
            issues.append({"severity": "error", "rule": "sign",
                           "message": f"成文日期格式不规范：『{date.strip()}』，应为『YYYY年M月D日』"})
    if not org and date:
        issues.append({"severity": "info", "rule": "sign",
                       "message": "有成文日期但缺少落款机关名称"})
    if org and not date:
        issues.append({"severity": "warning", "rule": "sign",
                       "message": "有落款机关但缺少成文日期"})

    # 5) 日期位置（应靠近文末）
    if date:
        last_nonempty = max((i for i, l in enumerate(lines, 1) if l.strip()), default=0)
        date_line = next((i for i, l in enumerate(lines, 1) if l.strip() == date.strip()), 0)
        if date_line and last_nonempty - date_line > 2:
            issues.append({"severity": "warning", "rule": "sign",
                           "message": "成文日期不在文末，建议置于落款之下、紧邻结尾"})

    # 6) 正文缩进建议（非层级、非落款的段落建议首行缩进 2 字）
    suggest_indent = 0
    for l in body:
        s = l.strip()
        if not s:
            continue
        if H1_RE.match(s) or H2_RE.match(s) or H3_RE.match(s):
            continue
        if s == org or s == date:
            continue
        if not s.startswith((" ", "　")):
            suggest_indent += 1
    if suggest_indent:
        issues.append({"severity": "info", "rule": "indent",
                       "message": f"约{suggest_indent}个正文段落建议首行缩进2字符（公文正文规范）"})

    err = sum(1 for x in issues if x["severity"] == "error")
    warn = sum(1 for x in issues if x["severity"] == "warning")
    score = max(0, 100 - err * 15 - warn * 5)
    level = "合格" if err == 0 and warn == 0 else ("基本合格" if err == 0 else "待整改")
    normalized = format_document(content)
    changed = normalized.strip() != content.strip()
    return {"score": score, "level": level, "issues": issues,
            "normalized": normalized, "changed": changed}
