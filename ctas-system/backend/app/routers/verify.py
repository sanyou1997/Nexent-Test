"""离线文书校验（规则引擎，无需联网、无需 LLM）。

校验维度：
1. 错别字：高频混淆词词典 + 上下文规则（的/得/地、截至/截止、处以/处于等）
2. 标点符号：英文标点混用、堆叠、引号/书名号配对、省略号、段落结尾（GB/T 15834）
3. 公文格式规范：标题、成文日期、落款、层级编号连续性
4. 占位符/待补充残留
5. 条款引用有效性：文中《法规名》第X条 是否存在于本机法规库
6. 规范表述：口语化/绝对化/数字规范；密级与保密提示

支持纯文本与文件（.docx / .xlsx / .txt / .md）离线校验。
"""
import bisect
import difflib
import io
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import audit, gongwen, proofread
from app.database import get_db
from app.deps import get_current_user
from app.models import User, RegulationArticle, Regulation, KeywordWhitelist, VerifyRule
from app.schemas import VerifyIn

router = APIRouter(prefix="/api/verify", tags=["A5 文书校验"])

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB


# ---- 规则集 ----

# ---- 规则集（数据驱动：词表迁至 verify_rule 表，业务可在界面增删/开关）----

def _check_rules(content: str, db: Session) -> list[dict]:
    """数据驱动的规范表述/敏感词/政治表述/数字金额/上下文校验。

    扫描 VerifyRule 表中 enabled 的规则：literal 词整词匹配，is_regex=True 时按正则匹配
    （re.IGNORECASE）。命中即产出一条校验项（level 决定必改/建议/提示），并带 pos/len
    供调用方映射回段落做高亮。
    """
    items: list[dict] = []
    try:
        rules = db.query(VerifyRule).filter(VerifyRule.enabled.is_(True)).all()
    except Exception:
        return items
    for r in rules:
        try:
            pat = re.compile(r.term, re.IGNORECASE) if r.is_regex else re.compile(re.escape(r.term))
        except re.error:
            continue
        for m in pat.finditer(content):
            items.append({"level": r.level, "category": r.category,
                          "pos": m.start(), "len": len(m.group(0)),
                          "message": f"「{m.group(0)}」{r.suggestion}"})
    return items


def _check_format(content: str) -> list[dict]:
    items = []
    title, org, date, body = gongwen.split_structure(content)
    if not title:
        items.append({"level": "error", "category": "公文格式",
                      "message": "未识别到文书标题（应为首行居中的概括性标题）"})
    elif len("".join(title)) > 44:
        items.append({"level": "warn", "category": "公文格式",
                      "message": f"标题过长（{''.join(title)[:44]}…，建议不超过 40 字）"})
    if not date:
        items.append({"level": "warn", "category": "公文格式", "message": "未识别到成文日期（落款处应含'YYYY年MM月DD日'）"})
    if not org:
        items.append({"level": "warn", "category": "公文格式", "message": "未识别到落款机关署名"})
    # 层级编号连续性：一、二、三…
    seq = [len(m.group(1)) if m.group(1) != "十" else 10
           for m in re.finditer(r"^([一二三四五六七八九十]+)、", content, re.M)]
    if len(seq) >= 2:
        for a, b in zip(seq, seq[1:]):
            if b != a + 1:
                idx = "一二三四五六七八九十"[min(b - 1, 9)]
                items.append({"level": "warn", "category": "公文格式",
                              "message": f"一级标题编号疑似跳跃（{a} 之后出现 {b}），请核对层级顺序"})
                break
    # 正文长度
    text = content.strip()
    if 0 < len(text) < 100:
        items.append({"level": "info", "category": "完整性", "message": "正文内容过短，请确认文书要素是否完整"})
    return items


def _check_placeholder(content: str) -> list[dict]:
    items = []
    for m in re.finditer(r"\{\{([^}]+)\}\}", content):
        items.append({"level": "error", "category": "占位符残留", "pos": m.start(), "len": len(m.group(0)),
                      "message": f"模板占位符 {{{{{m.group(1)}}}}} 未填写"})
    for m in re.finditer(r"〔待补充〕|\[待补充\]", content):
        items.append({"level": "error", "category": "占位符残留", "pos": m.start(), "len": len(m.group(0)),
                      "message": "存在「待补充」标记，正式文书不得留有待补充内容"})
    m = re.search(r"X{2,}(某|先生|女士|同志)?", content)
    if m:
        items.append({"level": "warn", "category": "占位符残留", "pos": m.start(), "len": len(m.group(0)),
                      "message": f"疑似匿名占位写法「{m.group(0)}」，正式文书应使用真实姓名或规范隐名（如'张某'）"})
    return items


def _check_citations(content: str, db: Session) -> list[dict]:
    """文中引用的《法规名》第X条 是否能在本机法规库中找到。"""
    items = []
    # 建立法规名 → id 映射（含简称匹配）
    regs = db.query(Regulation).all()
    hits = re.finditer(r"《([^《》]{2,30})》\s*(?:第|[其]第)\s*([一二三四五六七八九十百零\d]+)\s*条", content)
    checked = 0
    for m in hits:
        name, no = m.group(1), f"第{m.group(2)}条"
        checked += 1
        # 匹配法规（全名或包含简称）
        reg = next((r for r in regs if name in r.title or r.title in name or
                    (len(name) >= 6 and name[:6] in r.title)), None)
        if not reg:
            items.append({"level": "info", "category": "条款引用",
                          "message": f"《{name}》不在本机法规库中，无法自动核验 {no}，请人工核对现行有效版本"})
            continue
        exists = db.query(RegulationArticle).filter(
            RegulationArticle.regulation_id == reg.id,
            RegulationArticle.article_no == no).count()
        if not exists:
            items.append({"level": "error", "category": "条款引用",
                          "message": f"《{reg.title}》{no} 未在本机法规库中找到，请核对条款号是否准确（或导入该法规全文）"})
    if not checked:
        items.append({"level": "info", "category": "条款引用",
                      "message": "未检测到具体条款引用（文书一般应引用处分依据条款）"})
    return items


# 注：规范表述/敏感词/政治表述/数字金额/上下文 校验已迁移至数据驱动的 _check_rules()，
# 词表由 verify_rule 表提供（见 _seed_verify_rules 预置 + 界面可编辑），不再使用硬编码常量。


def _check_security(content: str) -> list[dict]:
    items = []
    if not re.search(r"密\s*级|绝密|机密|秘密|内部", content):
        items.append({"level": "info", "category": "保密要求",
                      "message": "未标注密级：审查调查文书一般应按内部资料管理并标注密级/使用范围"})
    return items


def _check_docx_style(data: bytes) -> list[dict]:
    """解析 Word 文档的排版合规性：GB/T 9704-2012 简化版。

    检查项：页面(A4/页边距)、标题(二号方正小标宋居中)、正文(三号仿宋/首行缩进2字/固定行距28.9磅)、
    一级标题(黑体)、二级标题(楷体)、落款与成文日期(仿宋右置)。标准常数取自 gongwen.GONGWEN_STD。
    仅对上传的 .docx 生效；纯文本无字体信息，由调用方提示上传 docx。
    """
    from docx import Document as Docx
    from docx.oxml.ns import qn
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING

    S = gongwen.GONGWEN_STD
    try:
        d = Docx(io.BytesIO(data))
    except Exception:
        return []
    items: list[dict] = []

    def cm(v):
        return round(v / 360000, 2) if v is not None else None

    def add(level, message, locate="", context=""):
        items.append({"level": level, "category": "公文排版", "message": message,
                      "locate": locate, "context": context})

    # 1) 页面：A4 尺寸 + 页边距
    for sec in d.sections:
        pw = cm(sec.page_width) if sec.page_width else None
        ph = cm(sec.page_height) if sec.page_height else None
        if pw is not None and abs(pw - 21.0) > 0.3:
            add("warn", f"页面宽度 {pw}cm 非标准 A4（21cm）", "页面")
        if ph is not None and abs(ph - 29.7) > 0.3:
            add("warn", f"页面高度 {ph}cm 非标准 A4（29.7cm）", "页面")
        top, bot = cm(sec.top_margin), cm(sec.bottom_margin)
        left, right = cm(sec.left_margin), cm(sec.right_margin)
        std = (S["page_top_cm"], S["page_bottom_cm"], S["page_left_cm"], S["page_right_cm"])
        actual = (top, bot, left, right)
        for nm, a, s in zip(("上", "下", "左", "右"), actual, std):
            if a is not None and abs(a - s) > 0.6:
                add("warn", f"页边距「{nm}」为 {a}cm，与公文标准 {s}cm 偏差较大，建议调整", "页面")

    # 段落级样式解析辅助
    def para_font_size(p):
        fonts, sizes = [], []
        for r in p.runs:
            rpr = r._element.rPr
            if rpr is not None and rpr.rFonts is not None:
                ea = rpr.rFonts.get(qn("w:eastAsia"))
                if ea:
                    fonts.append(str(ea))
            if r.font.size:
                sizes.append(r.font.size.pt)
        dom_font = max(set(fonts), key=fonts.count) if fonts else None
        dom_size = max(set(sizes), key=sizes.count) if sizes else None
        return dom_font, dom_size

    def line_spacing_pt(p):
        pf = p.paragraph_format
        if pf.line_spacing_rule == WD_LINE_SPACING.EXACTLY and pf.line_spacing:
            try:
                # python-docx 对 EXACTLY 规则返回的是 EMU（1pt = 12700 EMU），需换算为磅
                return float(pf.line_spacing) / 12700.0
            except Exception:
                return None
        return None

    def in_table(p):
        parent = p._p.getparent()
        return parent is not None and parent.tag.endswith("}tc")

    # 2) 段落：标题 / 层级标题 / 落款 / 正文
    for i, p in enumerate(d.paragraphs, 1):
        text = p.text.strip()
        if not text or in_table(p):
            continue
        dom_font, dom_size = para_font_size(p)
        pf = p.paragraph_format
        align = pf.alignment
        is_date = bool(gongwen.DATE_RE.match(text))
        loc = f"第{i}段"
        ctx = text[:30]

        # 标题：居中且非日期、非层级编号
        if align == WD_ALIGN_PARAGRAPH.CENTER and not is_date \
                and not gongwen.H1_RE.match(text) and not gongwen.H2_RE.match(text):
            if dom_size is not None and abs(dom_size - S["title_size_pt"]) > 1.5:
                add("warn", f"{loc}标题字号为 {dom_size}pt，公文标题应为二号（22pt）", loc, ctx)
            if dom_font and not any(k in dom_font for k in S["title_fonts"]):
                add("info", f"{loc}标题字体为「{dom_font}」，公文标题应为方正小标宋简体", loc, ctx)
            continue

        # 一级标题 一、
        if gongwen.H1_RE.match(text):
            if dom_font and not any(k in dom_font for k in S["h1_fonts"]):
                add("info", f"{loc}一级标题字体为「{dom_font}」，应为黑体", loc, ctx)
            if dom_size is not None and abs(dom_size - S["h1_size_pt"]) > 1.5:
                add("info", f"{loc}一级标题字号 {dom_size}pt，应为三号（16pt）", loc, ctx)
            continue

        # 二级标题 （一）
        if gongwen.H2_RE.match(text):
            if dom_font and not any(k in dom_font for k in S["h2_fonts"]):
                add("info", f"{loc}二级标题字体为「{dom_font}」，应为楷体（楷体_GB2312）", loc, ctx)
            if dom_size is not None and abs(dom_size - S["h2_size_pt"]) > 1.5:
                add("info", f"{loc}二级标题字号 {dom_size}pt，应为三号（16pt）", loc, ctx)
            continue

        # 落款 / 成文日期（右对齐）
        if align == WD_ALIGN_PARAGRAPH.RIGHT:
            if dom_font and not any(k in dom_font for k in S["body_fonts"]):
                add("info", f"{loc}落款/日期字体为「{dom_font}」，应为仿宋", loc, ctx)
            if dom_size is not None and abs(dom_size - S["body_size_pt"]) > 1.5:
                add("info", f"{loc}落款/日期字号 {dom_size}pt，应为三号（16pt）", loc, ctx)
            continue

        # 正文
        if dom_font and not any(k in dom_font for k in S["body_fonts"]):
            add("info", f"{loc}正文字体为「{dom_font}」，公文正文应为仿宋_GB2312（三号）", loc, ctx)
        if dom_size is not None and abs(dom_size - S["body_size_pt"]) > 1.5:
            add("warn", f"{loc}正文为 {dom_size}pt，公文正文应为三号（16pt）", loc, ctx)
        need = (dom_size if dom_size is not None else S["body_size_pt"]) * S["first_line_indent_chars"]
        if pf.first_line_indent is None or (pf.first_line_indent.pt or 0) < need * 0.8:
            add("warn", f"{loc}正文首行未缩进 2 字符（公文要求首行缩进 2 字）", loc, ctx)
        ls = line_spacing_pt(p)
        if ls is None:
            add("info", f"{loc}正文行距非固定值，建议设为固定值 28.9 磅", loc, ctx)
        elif abs(ls - S["line_spacing_pt"]) > 1.0:
            add("info", f"{loc}正文行距 {ls} 磅，建议固定值 28.9 磅", loc, ctx)
    return items


def _check_coherence(text: str) -> list[dict]:
    """文本连贯性（离线规则）：重复句、雷同句、超长句。"""
    from collections import Counter
    sents = [s.strip() for s in re.split(r"[。！？；\n]+", text) if len(s.strip()) >= 8]
    items: list[dict] = []
    # 重复句
    for s, c in Counter(x for x in sents if len(x) >= 12).items():
        if c > 1:
            items.append({"level": "error", "category": "文本连贯性",
                          "message": f"存在重复语句（出现 {c} 次）：「{s[:40]}…」",
                          "locate": "", "context": s[:40]})
    # 雷同句（近似）
    uniq = list(dict.fromkeys(sents))
    for a in uniq:
        if len(a) < 15:
            continue
        for b in uniq:
            if b == a or len(b) < 15:
                continue
            if difflib.SequenceMatcher(None, a, b).ratio() > 0.85:
                items.append({"level": "warn", "category": "文本连贯性",
                              "message": f"疑似雷同/重复表述：「{a[:28]}…」与「{b[:28]}…」高度相似，请核对是否冗余",
                              "locate": "", "context": a[:28]})
                break
    # 超长句
    for s in sents:
        if len(s) > 200:
            items.append({"level": "info", "category": "文本连贯性",
                          "message": f"单句过长（{len(s)} 字），建议拆分以提升可读性与逻辑层次",
                          "locate": "", "context": s[:40]})
    return items


def _check_coherence_llm(text: str, user=None) -> list[dict]:
    """联网时的整体连贯性/逻辑矛盾复核（无 Key 自动跳过，走离线规则）。"""
    from app.llm.provider import chat_json, llm_available
    if not llm_available():
        return []
    if len(text) > 6000:
        text = text[:6000]
    prompt = [
        {"role": "system", "content": "你是纪检监察公文审核专家，只输出 JSON，不要解释。"},
        {"role": "user", "content": (
            "请审阅以下纪检监察文书，重点检查：文本连贯性、逻辑一致性、前后矛盾、"
            "事实表述是否清晰、是否存在重复或冗余。返回 JSON："
            "{\"issues\":[{\"level\":\"error|warn|info\",\"message\":\"问题描述与修改建议\"}]}，"
            "最多 8 条，只返回 JSON：\n\n" + text)}
    ]
    data = chat_json(prompt, scene="verify", user_id=user.id if user else None)
    if not data or not isinstance(data.get("issues"), list):
        return []
    out: list[dict] = []
    for it in data["issues"][:8]:
        lvl = it.get("level") if it.get("level") in ("error", "warn", "info") else "info"
        msg = str(it.get("message", "")).strip()
        if msg:
            out.append({"level": lvl, "category": "文本连贯性(智能)", "message": msg,
                        "locate": "", "context": ""})
    return out


@router.post("")
def verify(data: VerifyIn, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    content = (data.content or "").strip()
    if not content:
        return {"items": [{"level": "error", "category": "输入", "message": "文书内容为空"}],
                "score": 0, "stats": {"chars": 0}}
    segs = _segments_from_text(content)
    return _run_checks(segs, db, content, do_format=True, user=user, source=None, docx_bytes=None)


def _auto_fix(content: str) -> tuple[str, list[dict]]:
    """一键修正：仅做确定性、零语义风险的排版/标点/规范表述修正，绝不改动措辞含义。

    覆盖：英文标点转中文、英文引号配对、标点堆叠去重、省略号规范、时间范围一字线、
    上下文规则（的→得/地、截止→截至、必需→必须、处于→处以、反应→反映、权利→权力）。
    注：错别字词典修正不在此处（错别字校对已隐藏），避免无提示改动正文。
    """
    fixes: list[dict] = []
    out = content

    # 1) 中文语境英文标点 -> 中文全角
    ptable = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！"}
    new, n = re.subn(rf"(?<=[\u4e00-\u9fff])[ \t]*([,;:?!])[ \t]*(?=[\u4e00-\u9fff])",
                     lambda m: ptable[m.group(1)], out)
    if n:
        fixes.append({"type": "标点", "desc": "英文标点（, ; : ? !）转为中文全角", "count": n})
        out = new
    new, n = re.subn(rf"(?<=[\u4e00-\u9fff])[.](?=[\u4e00-\u9fff]|$)", "。", out)
    if n:
        fixes.append({"type": "标点", "desc": "英文句点「.」转为中文「。」", "count": n})
        out = new

    # 2) 英文双引号配对 -> 中文引号
    if '"' in out:
        parts = out.split('"')
        f = parts[0]
        for i in range(1, len(parts)):
            f += ("“" if i % 2 == 1 else "”") + parts[i]
        fixes.append({"type": "标点", "desc": "英文双引号转为中文引号“…”", "count": out.count('"')})
        out = f

    # 3) 标点堆叠去重
    new, n = re.subn(r"([，。；：、！？])\1+", r"\1", out)
    if n:
        fixes.append({"type": "标点", "desc": "去除重复标点（如“。。”）", "count": n})
        out = new
    new, n = re.subn(r"[。；：][，]|[，][。；：]", lambda m: m.group(0)[0], out)
    if n:
        fixes.append({"type": "标点", "desc": "修正标点堆叠（如“，。”）", "count": n})
        out = new

    # 4) 省略号规范
    new, n = re.subn(r"\.{2,}|…(?!…)", "……", out)
    if n:
        fixes.append({"type": "标点", "desc": "规范省略号为「……」", "count": n})
        out = new

    # 5) 时间范围一字线
    new, n = re.subn(r"(\d+\s*[年月日])\s*[-–]\s*(?=\d)", r"\1—", out)
    if n:
        fixes.append({"type": "格式", "desc": "时间范围连接符改为一字线「—」", "count": n})
        out = new

    # 6) 上下文规则（确定性）
    ctx = [
        (r"(做|办|搞|干|说|写|执行|完成|处理|改|查|看|听|学|研究|抓)的(很|较|不|非常|十分|更|还|多|好|快|出|差)",
         r"\1得\2", "动词后程度补语「的」改为「得」"),
        (r"(慢慢|静静|渐渐|悄悄|认真|努力|仔细|深入|系统|全面)的(说|走|看|听|学|做|干|研究|检查|工作|调查|分析|思考|落实|推进)",
         r"\1地\2", "状语「的」改为「地」"),
        (r"截止(?=\s*(?:\d{4}\s*年|\d+\s*[月日]))", "截至", "后接时间点「截止」改为「截至」"),
        (r"必需(?![品物费经])", "必须", "后接动词「必需」改为「必须」"),
        (r"处于(?=(警告|严重警告|记过|记大过|降级|撤职|开除|留置))", "处以", "处分表述「处于」改为「处以」"),
        (r"反应(?=(情况|问题|意见|线索|诉求|群众|举报|反映))", "反映", "报送表述「反应」改为「反映」"),
        (r"权利(?=(机关|部门|行使|滥用|公))", "权力", "公权表述「权利」改为「权力」"),
    ]
    for pat, repl, desc in ctx:
        new, n = re.subn(pat, repl, out)
        if n:
            fixes.append({"type": "规范表述", "desc": desc, "count": n})
            out = new

    return out, fixes


@router.post("/fix")
def fix_verify(data: VerifyIn, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    """一键排版修正：确定性修正标点/格式/规范表述，返回修正后全文与修正清单。"""
    content = (data.content or "").strip()
    if not content:
        raise HTTPException(400, "文书内容为空")
    fixed, fixes = _auto_fix(content)
    audit.write_log(db, user.id, user.username, action="document_fix",
                    detail={"applied": len(fixes), "changed": fixed != content, "chars": len(content)})
    return {"fixed": fixed, "fixes": fixes, "applied": len(fixes),
            "changed": fixed != content}


@router.post("/file")
async def verify_file(file: UploadFile = File(...), db: Session = Depends(get_db),
                      user: User = Depends(get_current_user)):
    """上传 Word/Excel/文本文件做离线校验。"""
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, "文件超过 20MB 限制")
    name = file.filename or "未命名"
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""

    if ext == "docx":
        segs = _extract_docx(data)
        do_format = True
    elif ext == "xlsx":
        segs = _extract_xlsx(data)
        do_format = False  # 表格不做公文结构检查，但保留错别字/标点/表述
    elif ext in ("txt", "md"):
        segs = _segments_from_text(data.decode("utf-8", errors="ignore"))
        do_format = True
    elif ext in ("doc", "xls"):
        raise HTTPException(400, "暂不支持旧版 .doc/.xls 格式，请在 Word/Excel 中另存为 .docx/.xlsx 后上传")
    else:
        raise HTTPException(400, "支持的格式：.docx / .xlsx / .txt / .md")

    if not segs:
        return {"items": [{"level": "error", "category": "输入",
                           "message": f"文件「{name}」中未提取到文本内容"}],
                "score": 0, "stats": {"chars": 0}, "source": name, "file_type": ext}
    full_text = "\n".join(t for _, t in segs)
    return _run_checks(segs, db, full_text, do_format=do_format, user=user, source=name,
                        docx_bytes=data if ext == "docx" else None)


# ---- 受保护关键词（用户自定义白名单）----
@router.get("/keywords")
def list_keywords(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """列出用户自定义受保护关键词（校对时跳过，避免重复命中领域常用词）。"""
    rows = db.query(KeywordWhitelist).order_by(KeywordWhitelist.id.desc()).all()
    return [{"id": r.id, "keyword": r.keyword, "note": r.note,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""}
            for r in rows]


@router.post("/keywords")
def add_keyword(body: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    kw = (body.get("keyword") or "").strip()
    if not kw:
        raise HTTPException(400, "关键词不能为空")
    if len(kw) > 50:
        raise HTTPException(400, "关键词过长（≤50 字）")
    if db.query(KeywordWhitelist).filter(KeywordWhitelist.keyword == kw).first():
        raise HTTPException(409, f"关键词「{kw}」已存在")
    row = KeywordWhitelist(keyword=kw, note=(body.get("note") or "").strip(),
                           created_by=user.id)
    db.add(row)
    db.commit()
    audit.write_log(db, user.id, user.username, action="keyword_add",
                    resource_type="keyword", resource_id=row.id, detail={"keyword": kw})
    return {"id": row.id, "keyword": row.keyword, "note": row.note}


@router.delete("/keywords/{kid}")
def delete_keyword(kid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(KeywordWhitelist, kid)
    if not row:
        raise HTTPException(404, "关键词不存在")
    db.delete(row)
    db.commit()
    audit.write_log(db, user.id, user.username, action="keyword_delete",
                    resource_type="keyword", resource_id=kid, detail={"keyword": row.keyword})
    return {"ok": True}


# ---- 文书质检规则库（数据驱动，业务可维护）----
RULE_CATS = ["规范表述", "绝对化表述", "敏感规范词", "政治表述", "数字金额规范", "上下文"]


@router.get("/rules")
def list_rules(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """列出全部质检规则（按类别、内置优先）。"""
    rows = db.query(VerifyRule).order_by(VerifyRule.category, VerifyRule.id).all()
    return [{
        "id": r.id, "category": r.category, "level": r.level, "term": r.term,
        "suggestion": r.suggestion, "is_regex": r.is_regex, "enabled": r.enabled,
        "builtin": r.builtin,
        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
    } for r in rows]


@router.post("/rules")
def add_rule(body: dict, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """新增一条质检规则。term 为匹配词或正则（is_regex=true 时）。"""
    term = (body.get("term") or "").strip()
    if not term:
        raise HTTPException(400, "匹配词/正则不能为空")
    if len(term) > 120:
        raise HTTPException(400, "匹配词过长（≤120 字）")
    cat = body.get("category") or "规范表述"
    level = body.get("level") or "warn"
    if level not in ("error", "warn", "info"):
        raise HTTPException(400, "level 须为 error/warn/info")
    if body.get("is_regex"):
        try:
            re.compile(term)
        except re.error as e:
            raise HTTPException(400, f"正则表达式无效：{e}")
    row = VerifyRule(
        category=cat, level=level, term=term,
        suggestion=(body.get("suggestion") or "").strip(),
        is_regex=bool(body.get("is_regex")), enabled=body.get("enabled", True),
        builtin=False, created_by=user.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    audit.write_log(db, user.id, user.username, action="verify_rule_add",
                    resource_type="verify_rule", resource_id=row.id,
                    detail={"category": cat, "term": term})
    return {"id": row.id, "term": row.term, "category": row.category, "enabled": row.enabled}


@router.put("/rules/{rid}")
def update_rule(rid: int, body: dict, db: Session = Depends(get_db),
                user: User = Depends(get_current_user)):
    """编辑/开关一条规则。"""
    row = db.get(VerifyRule, rid)
    if not row:
        raise HTTPException(404, "规则不存在")
    if "enabled" in body:
        row.enabled = bool(body["enabled"])
    if "term" in body:
        t = (body["term"] or "").strip()
        if not t:
            raise HTTPException(400, "匹配词/正则不能为空")
        if len(t) > 120:
            raise HTTPException(400, "匹配词过长（≤120 字）")
        row.term = t
    if "category" in body and body["category"]:
        row.category = body["category"]
    if "level" in body and body["level"] in ("error", "warn", "info"):
        row.level = body["level"]
    if "suggestion" in body:
        row.suggestion = (body["suggestion"] or "").strip()
    if "is_regex" in body:
        row.is_regex = bool(body["is_regex"])
        if row.is_regex:
            try:
                re.compile(row.term)
            except re.error as e:
                raise HTTPException(400, f"正则表达式无效：{e}")
    db.commit()
    audit.write_log(db, user.id, user.username, action="verify_rule_update",
                    resource_type="verify_rule", resource_id=rid,
                    detail={"term": row.term, "enabled": row.enabled})
    return {"id": row.id, "enabled": row.enabled}


@router.delete("/rules/{rid}")
def delete_rule(rid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(VerifyRule, rid)
    if not row:
        raise HTTPException(404, "规则不存在")
    db.delete(row)
    db.commit()
    audit.write_log(db, user.id, user.username, action="verify_rule_delete",
                    resource_type="verify_rule", resource_id=rid, detail={"term": row.term})
    return {"ok": True}


# ---- 文本分段与文件解析 ----

def _segments_from_text(content: str) -> list[tuple[str, str]]:
    """纯文本 → (第N段, 段文本) 列表（保留非空段落）。"""
    segs = []
    for i, line in enumerate(content.replace("\r\n", "\n").split("\n"), 1):
        if line.strip():
            segs.append((f"第{i}段", line.rstrip()))
    return segs


def _extract_docx(data: bytes) -> list[tuple[str, str]]:
    """Word 文档 → 段落 + 表格文本。"""
    from docx import Document as Docx
    d = Docx(io.BytesIO(data))
    segs = []
    for i, p in enumerate(d.paragraphs, 1):
        if p.text.strip():
            segs.append((f"第{i}段", p.text.strip()))
    for ti, table in enumerate(d.tables, 1):
        for ri, row in enumerate(table.rows, 1):
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                segs.append((f"表格{ti}·第{ri}行", " | ".join(cells)))
    return segs


def _extract_xlsx(data: bytes) -> list[tuple[str, str]]:
    """Excel 工作簿 → 逐单元格文本（定位：Sheet名!单元格坐标）。"""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    segs = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and v.strip():
                    segs.append((f"{ws.title}!{cell.coordinate}", v.strip()))
    wb.close()
    return segs


MAX_SEGMENTS_OUT = 600  # 返回给前端做对照渲染的最大段落/单元格数


def _segments_offsets(texts: list[str]) -> list[int]:
    """每段文本在拼接全文中的起始偏移（段间以 \n 连接）。"""
    offsets, cur = [], 0
    for t in texts:
        offsets.append(cur)
        cur += len(t) + 1
    return offsets


def _run_checks(segs: list[tuple[str, str]], db: Session, full_text: str,
                do_format: bool, user: User, source: str | None,
                docx_bytes: bytes | None = None) -> dict:
    texts = [t for _, t in segs]
    offsets = _segments_offsets(texts)
    items: list[dict] = []

    # 用户自定义受保护关键词（避免重复命中领域常用词）；领域术语白名单在 csc 模块内加载
    try:
        protected_kw = [kw.keyword for kw in db.query(KeywordWhitelist).all()]
    except Exception:
        protected_kw = []

    # ---- 全文级检查（占位符/表述等带 pos，随后映射到段落）----
    doc_items: list[dict] = []
    if do_format:
        items += _check_format(full_text)
        doc_items += _check_placeholder(full_text)
        items += _check_citations(full_text, db)
        doc_items += _check_rules(full_text, db)
        items += _check_security(full_text)

    for it in doc_items:
        pos, ln = it.pop("pos", None), it.pop("len", None)
        if pos is None:
            it["seg_index"], it["start"], it["end"] = -1, -1, -1
        else:
            si = max(0, bisect.bisect_right(offsets, pos) - 1)
            it["seg_index"], it["start"] = si, pos - offsets[si]
            it["end"] = it["start"] + (ln or 0)
            it["locate"] = segs[si][0] if si < len(segs) else ""
        it.setdefault("context", "")
        items.append(it)

    # ---- 逐段落/单元格：错别字 + 标点（表格行/单元格不做"结尾缺标点"检查）----
    is_para = source is None or not source.endswith(".xlsx")
    for idx, (locate, text) in enumerate(segs):
        seg_items = proofread.check_typos(text, locate, protected=protected_kw)
        # 精确词典已命中（error/上下文 warn）的位置交给 NLP 层跳过，避免重复报警
        covered = {(it["start"], it["end"]) for it in seg_items
                   if it.get("start", -1) >= 0 and it.get("end", -1) > it.get("start", -1)}
        seg_items += proofread.check_nlp_typos(text, locate, covered=covered, protected=protected_kw)
        seg_items += proofread.check_punct(text, locate,
                                           is_paragraph=is_para and not locate.startswith("表格"))
        for it in seg_items:
            it["seg_index"] = idx
        items += seg_items

    # 全文级复核：跨段引号配对（段落级检查会漏掉跨段引用）
    if full_text.count("“") != full_text.count("”"):
        items.append({"level": "error", "category": "标点符号", "locate": "全文",
                      "message": "全文双引号不配对，请检查是否漏掉开引号或闭引号",
                      "seg_index": -1, "start": -1, "end": -1, "context": ""})

    # ---- DOCX 排版/字体/页边距检查（仅上传 docx 时）----
    if docx_bytes:
        items += _check_docx_style(docx_bytes)
    # ---- 纯文本无法校验字体排版，提示上传 docx ----
    elif do_format and source and source.lower().endswith((".txt", ".md")):
        items.append({"level": "info", "category": "公文排版", "locate": "",
                      "message": "当前为纯文本，不含字体/字号/页边距信息，无法做排版合规性"
                                 "（二号小标宋/三号仿宋等）检查；如需请上传 .docx 文件",
                      "seg_index": -1, "start": -1, "end": -1, "context": ""})
    # ---- 文本连贯性（离线规则 + 联网智能复核）----
    if do_format:
        items += _check_coherence(full_text)
        items += _check_coherence_llm(full_text, user)

    # 无位置的全文级问题补齐字段，便于前端统一处理
    for it in items:
        it.setdefault("seg_index", -1)
        it.setdefault("start", -1)
        it.setdefault("end", -1)
        it.setdefault("locate", "")

    penalty = sum({"error": 12, "warn": 5, "info": 0}[i["level"]] for i in items)
    score = max(0, 100 - penalty)
    stats = {
        "chars": len(full_text),
        "segments": len(segs),
        "errors": sum(1 for i in items if i["level"] == "error"),
        "warns": sum(1 for i in items if i["level"] == "warn"),
        "infos": sum(1 for i in items if i["level"] == "info"),
    }
    # 对照视图原文（超长时截断，避免响应过大）
    out_segs = [{"locate": l, "text": t} for l, t in segs[:MAX_SEGMENTS_OUT]]
    result = {"items": items, "score": score, "stats": stats, "source": source,
              "segments": out_segs, "truncated": len(segs) > MAX_SEGMENTS_OUT}
    audit.write_log(db, user.id, user.username, action="document_verify",
                    detail={"chars": len(full_text), "score": score, "source": source,
                            "errors": stats["errors"], "warns": stats["warns"]})
    return result
