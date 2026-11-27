"""银行流水清洗与分析（纪检监察资金审查场景）。

本轮重构直击痛点：各种银行格式「快速 + 完整」导入。
- 解析：自动识别编码（utf-8-sig/gbk/gb18030）、自动定位表头行（解决多行标题/账户信息前置/合计行）；
- 列映射三级回退：① 关键词命中 → ② LLM 智能推断（离线环境下自动跳过）→ ③ 人工映射；
- 标准化：日期（含 Excel 序列号/多种中文格式）、金额（单边/收支出两列/借贷标志/括号负/千分位）、去重；
- 完整性质检门禁：清洗后给出『数据全不全』量化报告（跳行原因、各字段缺失率、完整度评分），
  只有清晰知道数据完整，分析才有意义。

分析（可解释规则，仅提示不定性）：大额 / 整数大额 / 夜间 / 高频对手方 / 敏感摘要。
"""
import io
import re
from collections import defaultdict
from datetime import datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.audit import write_log
from app.models import BankStatement, User

router = APIRouter(prefix="/api/bank", tags=["Bank Statement"])

MAX_FILE_MB = 30
DEFAULT_THRESHOLD = 50000.0

# 标准字段 → 表头候选别名（关键词命中，覆盖主流银行导出叫法）
STD_ALIASES = {
    "date": ["交易日期", "日期", "记账日期", "入账日期", "业务日期", "交易时间"],
    "time": ["交易时间", "时间", "交易时刻", "记账时间"],
    "amount": ["交易金额", "金额", "发生额", "交易额", "金额(元)", "借贷发生额", "交易金额(元)", "交易人民币"],
    "in_amt": ["收入金额", "收入(元)", "贷方发生额", "本行收入", "贷方金额", "存入金额", "贷方"],
    "out_amt": ["支出金额", "支出(元)", "借方发生额", "本行支出", "借方金额", "付出金额", "提出金额", "借方"],
    "flag": ["收支标志", "借贷标志", "收/支", "借贷", "摘要标志", "发生额方向", "借贷方"],
    "summary": ["摘要", "备注", "用途", "交易摘要", "附言", "摘要说明", "业务摘要", "摘要/备注"],
    "counterparty": ["对方户名", "对方账户名称", "交易对手", "对方名称", "户名", "收款方名称", "付款方名称",
                     "对手方名称", "对方账号名称", "交易对手方"],
    "account": ["对方账号", "对方账户", "交易对手账号", "对手方账号", "对方卡号"],
    "balance": ["余额", "账户余额", "当前余额", "期末余额", "账户现有余额"],
}
# 关键字段（缺失将显著影响分析价值）
KEY_FIELDS = ["date", "amount", "in_amt", "out_amt", "counterparty", "summary", "account", "balance", "time"]
REQUIRED = ["date"]  # 至少要有日期；金额可由 in/out 两列替代
SENSITIVE_WORDS = ["礼金", "感谢费", "好处费", "辛苦费", "劳务费", "咨询费",
                   "借款", "还款", "货款", "分红", "佣金", "回扣"]

# 银行模板指纹（命中即标注模板名，便于复核与一键复用识别逻辑）
BANK_TEMPLATES = {
    "工商银行": ["交易日期", "交易时间", "交易金额", "借贷标志", "摘要", "对方户名", "对方账号", "账户余额"],
    "招商银行": ["交易日期", "交易时间", "交易金额", "收支标志", "摘要", "交易对手", "交易对手账号", "余额"],
    "建设银行": ["记账日期", "借方发生额", "贷方发生额", "摘要", "对方户名", "对方账号", "余额"],
    "农业银行": ["交易日期", "交易金额", "收/支", "摘要", "对方户名", "对方账号", "账户余额"],
    "对公通用": ["日期", "金额", "收入", "支出", "摘要", "户名", "账号", "余额"],
}
STD_LABELS = {
    "date": "交易日期", "time": "交易时间", "amount": "金额(单边)", "in_amt": "收入金额",
    "out_amt": "支出金额", "flag": "收支/借贷标志", "summary": "摘要", "counterparty": "对方户名",
    "account": "对方账号", "balance": "余额",
}


# ---------------- 读取（统一 header=None，交由后续定位表头）----------------
def _read_raw(name: str, data: bytes):
    if not data:
        raise HTTPException(400, "上传文件为空")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {MAX_FILE_MB}MB 限制")
    try:
        if name.lower().endswith(".csv"):
            last_err = None
            for enc in ("utf-8-sig", "gbk", "gb18030"):
                try:
                    return pd.read_csv(io.BytesIO(data), header=None, encoding=enc,
                                       dtype=str, on_bad_lines="skip", keep_default_na=False), enc
                except UnicodeDecodeError as e:
                    last_err = e
                    continue
            raise HTTPException(422, f"CSV 编码无法识别（已尝试 UTF-8/GBK/GB18030）：{last_err}")
        return pd.read_excel(io.BytesIO(data), header=None, dtype=str, engine="openpyxl"), "xlsx"
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"文件解析失败：{e}")


def _cells(row) -> list:
    return [("" if pd.isna(v) else str(v).strip()) for v in list(row)]


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


# ---------------- 表头定位 + 模板指纹 ----------------
def _detect_header(raw: pd.DataFrame):
    best_idx, best_score = 0, -1
    max_scan = min(15, len(raw))
    for i in range(max_scan):
        cells = [_norm(c) for c in _cells(raw.iloc[i]) if c]
        score = 0
        for aliases in STD_ALIASES.values():
            for a in aliases:
                if _norm(a) in cells:
                    score += 1
                    break
        if score > best_score:
            best_score, best_idx = score, i
    header_cells = [_cells(raw.iloc[best_idx])[j] for j in range(len(raw.columns))]
    # 模板指纹
    # 模板指纹：按「指纹覆盖率(ratio) 优先、绝对命中数次之」排序。
    # 为什么不能只比绝对命中数：工商(8列)与农业(7列)对本行表头都命中 7 列，
    # 并列时按字典序会误判成工商银行——而工行的判别列「借贷标志」其实并不存在。
    # 用覆盖率可让真正"列列对上"的模板胜出（农业 7/7=1.0 > 工商 7/8=0.875）。
    template = ""
    best_key = (0.0, 0)
    norm_header = [_norm(c) for c in header_cells if c]
    for tname, sig in BANK_TEMPLATES.items():
        hit = sum(1 for s in sig if any(_norm(s) in h for h in norm_header))
        ratio = hit / len(sig)
        if ratio >= 0.6 and hit >= 3 and (ratio, hit) > best_key:
            best_key, template = (ratio, hit), tname
    return best_idx, header_cells, template


def _suggest_mapping(header_cells: list):
    """关键词命中映射；返回 {std: colname} 与未命中（必填/重要）字段列表。"""
    norm_cells = {j: _norm(c) for j, c in enumerate(header_cells) if c}
    mapping = {}
    for std, aliases in STD_ALIASES.items():
        for j, nc in norm_cells.items():
            if any(_norm(a) in nc or nc in _norm(a) for a in aliases):
                mapping[std] = header_cells[j]
                break
    # 合并 in/out 为 amount 等价标记
    has_amount = "amount" in mapping
    has_two = "in_amt" in mapping and "out_amt" in mapping
    pending = []
    if not has_amount and not has_two:
        pending.append("amount（或同时提供 收入/支出 两列）")
    for f in ("date", "counterparty", "summary"):
        if f not in mapping:
            pending.append(STD_LABELS.get(f, f))
    return mapping, pending


def _llm_mapping(header_cells: list, samples: list) -> dict:
    """离线环境无算力：尝试调用大模型推断未命中字段，失败则返回空（交由人工映射）。"""
    try:
        from app.llm import provider  # 可选依赖
    except Exception:
        return {}
    try:
        prompt = (
            "你是银行流水表头识别助手。给定表头字段列表与样本行，请返回 JSON 映射，"
            "把每个表头字段对应到以下标准字段之一（无法对应则忽略该字段）："
            "date,time,amount,in_amt,out_amt,flag,summary,counterparty,account,balance。\n"
            f"表头字段：{header_cells}\n样本行：{samples[:3]}\n只返回 JSON 对象，键为标准字段，值为表头字段名。"
        )
        gen = provider.chat([{"role": "user", "content": prompt}], scene="extract", user_id=0)
        if not gen:
            return {}
        import json
        m = re.search(r"\{.*\}", gen, re.S)
        if not m:
            return {}
        obj = json.loads(m.group(0))
        valid = set(STD_ALIASES.keys())
        return {k: v for k, v in obj.items() if k in valid and v in header_cells}
    except Exception:
        return {}


def _parse_date(s: str) -> str:
    s = str(s).strip()
    if s in ("", "nan", "NaT", "None"):
        return ""
    m = re.match(r"(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)  # mm/dd/yyyy
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    if s.isdigit() and 20000 <= int(s) <= 70000:  # Excel 序列号
        d = datetime(1899, 12, 30) + timedelta(days=int(s))
        return d.strftime("%Y-%m-%d")
    return ""


def _parse_amount(s: str) -> float:
    s = str(s).strip()
    if s in ("", "nan", "NaT", "None"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
    s = re.sub(r"[^\d.]", "", s)
    try:
        v = float(s) if s not in ("", ".") else 0.0
    except ValueError:
        return 0.0
    return -v if neg else v


def _normalize(raw: pd.DataFrame, header_idx: int, mapping: dict, threshold: float):
    """按映射把 raw 从第 header_idx 行起解析为规范交易行，并产出完整性质检报告。"""
    header_cells = [_cells(raw.iloc[header_idx])[j] for j in range(len(raw.columns))]
    col_of = {std: header_cells.index(col) for std, col in mapping.items() if col in header_cells}

    def g(std, row_cells):
        idx = col_of.get(std)
        return row_cells[idx] if idx is not None and idx < len(row_cells) else ""

    rows, seen, dup = [], set(), 0
    src_rows = 0
    skipped = defaultdict(int)
    missing = {f: {"present": 0, "missing": 0} for f in STD_ALIASES}
    date_min, date_max = "", ""
    for i in range(header_idx + 1, len(raw)):
        src_rows += 1
        cells = _cells(raw.iloc[i])
        if not any(cells):
            skipped["空行"] += 1
            continue
        date = _parse_date(g("date", cells))
        # 金额获取
        if "in_amt" in col_of and "out_amt" in col_of:
            in_v, out_v = abs(_parse_amount(g("in_amt", cells))), abs(_parse_amount(g("out_amt", cells)))
            amount = max(in_v, out_v)
            is_out = out_v >= in_v and in_v > 0  # 两列都有值时取较大者方向
            if in_v == 0 and out_v == 0:
                amount = 0.0
        else:
            amt_raw = _parse_amount(g("amount", cells))
            amount = abs(amt_raw)
            flag = g("flag", cells)
            if flag:
                fl = flag
                if any(k in fl for k in ("收", "贷")) and "支" not in fl:
                    is_out = False
                elif any(k in fl for k in ("支", "借", "付", "出")):
                    is_out = True
                else:
                    is_out = amt_raw < 0
            else:
                is_out = amt_raw < 0
        if not date:
            skipped["无有效日期"] += 1
            continue
        if amount == 0:
            skipped["零金额"] += 1
            continue
        # 缺失统计（仅对解析成功的行）
        for f in STD_ALIASES:
            if f == "in_amt" or f == "out_amt":
                continue
            if f in col_of:
                if g(f, cells):
                    missing[f]["present"] += 1
                else:
                    missing[f]["missing"] += 1
        time_ = g("time", cells)
        cp = g("counterparty", cells)
        summary = g("summary", cells)
        acc = g("account", cells)
        bal = g("balance", cells)
        key = (date, time_, round(amount, 2), cp, summary, is_out)
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        if not date_min or date < date_min:
            date_min = date
        if not date_max or date > date_max:
            date_max = date
        rows.append({
            "date": date, "time": time_, "summary": summary, "counterparty": cp,
            "account": acc, "balance": bal, "amount": round(amount, 2),
            "in": 0.0 if is_out else round(amount, 2),
            "out": round(amount, 2) if is_out else 0.0,
        })
    # 完整度评分
    parsed = len(rows)
    skipped_total = sum(skipped.values())
    missing_report = {}
    score = 100.0
    weights = {"date": 22, "amount": 22, "counterparty": 14, "summary": 10,
               "account": 6, "balance": 6, "time": 4, "flag": 4, "in_amt": 11, "out_amt": 11}
    for f, w in weights.items():
        if f in ("in_amt", "out_amt"):
            if "in_amt" in col_of and "out_amt" in col_of:
                continue  # 两列齐备不扣分
            score -= w  # 缺少两列收入/支出，按单边金额处理（已扣 amount 权重，避免重复，这里仅轻扣）
            score += w * 0.5
            continue
        if f in col_of and parsed > 0:
            mr = missing[f]["missing"] / max(1, parsed)
            missing_report[f] = {"present": missing[f]["present"], "missing": missing[f]["missing"],
                                 "rate": round(mr, 3)}
            score -= w * mr
        else:
            missing_report[f] = {"present": 0, "missing": parsed, "rate": 1.0}
            score -= w
    score = max(0.0, min(100.0, round(score, 1)))
    skipped_ratio = skipped_total / max(1, src_rows)
    if score >= 92 and skipped_ratio <= 0.05:
        level = "高"
    elif score >= 78:
        level = "中"
    else:
        level = "低"
    guide = ""
    if level != "高":
        low_fields = [STD_LABELS.get(f, f) for f, v in missing_report.items()
                      if v["rate"] >= 0.4 and f in ("date", "amount", "counterparty", "summary")]
        if low_fields:
            guide = f"以下字段缺失率偏高（≥40%）：{ '、'.join(low_fields) }，建议核对表头映射或补充数据；" \
                    f"跳行 {skipped_total} 条（{skipped_ratio*100:.0f}%），原因：{ '、'.join(f'{k}×{v}' for k,v in skipped.items()) }。"
        else:
            guide = f"完整度中等，跳行 {skipped_total} 条（{skipped_ratio*100:.0f}%），仍可用于分析但建议复核映射。"
    qc = {
        "source_rows": src_rows, "parsed_rows": parsed, "skipped_rows": skipped_total,
        "skip_reasons": dict(skipped), "missing_report": missing_report,
        "date_range": [date_min, date_max], "dup_rows": dup,
        "skipped_ratio": round(skipped_ratio, 3), "score": score, "level": level, "guide": guide,
    }
    return rows, qc


def analyze(rows: list, threshold: float) -> dict:
    total_in = sum(r["in"] for r in rows)
    total_out = sum(r["out"] for r in rows)
    anomalies = []
    for r in rows:
        amt = max(r["in"], r["out"])
        if amt >= threshold:
            reasons = [f"单笔大额（{amt:,.0f} 元 ≥ 阈值 {threshold:,.0f}）"]
            if amt % 10000 == 0 and amt >= threshold:
                reasons.append("万元整数金额（疑似资金往来）")
            anomalies.append({"type": "大额交易", "reason": "；".join(reasons), **r})
    for r in rows:
        t = r.get("time", "")
        m = re.match(r"(\d{1,2}):", t)
        if m and (int(m.group(1)) >= 22 or int(m.group(1)) < 6):
            if max(r["in"], r["out"]) >= threshold * 0.2:
                anomalies.append({"type": "夜间交易", "reason": f"交易时间 {t}（22:00–06:00）", **r})
    cp = defaultdict(lambda: {"count": 0, "total": 0.0})
    for r in rows:
        if r["counterparty"]:
            cp[r["counterparty"]]["count"] += 1
            cp[r["counterparty"]]["total"] += max(r["in"], r["out"])
    risky_cps = []
    for name, v in cp.items():
        if v["count"] >= 5 or v["total"] >= threshold * 5:
            risky_cps.append({"counterparty": name, "count": v["count"], "total": round(v["total"], 2)})
    for r in rows:
        if r["counterparty"] in [c["counterparty"] for c in risky_cps]:
            anomalies.append({"type": "高频对手方",
                              "reason": f"与「{r['counterparty']}」交易密切（详见高频对手方统计）", **r})
    for r in rows:
        hit = [w for w in SENSITIVE_WORDS if w in r["summary"]]
        if hit:
            anomalies.append({"type": "敏感摘要", "reason": f"摘要含关键词：{'、'.join(hit)}", **r})

    # ---- 资金回流 / 闭环（纪检监察重点：同对手既收又付，疑似过账）----
    _cp_in, _cp_out = set(), set()
    for r in rows:
        if r["counterparty"]:
            if r["in"] > 0:
                _cp_in.add(r["counterparty"])
            if r["out"] > 0:
                _cp_out.add(r["counterparty"])
    _fund_return = _cp_in & _cp_out
    for r in rows:
        if r["counterparty"] in _fund_return:
            anomalies.append({"type": "资金回流",
                              "reason": f"「{r['counterparty']}」在本批次同时有收入与支出，疑似资金过账/回流，须穿透核查资金去向", **r})

    # ---- 余额链完整性（仅当余额列有效且呈连续流水）----
    _bal_rows = [r for r in rows if isinstance(r.get("balance"), (int, float))]
    if len(_bal_rows) >= 3:
        _prev = None
        _bad = 0
        for r in rows:
            b = r.get("balance")
            if not isinstance(b, (int, float)):
                _prev = None
                continue
            if _prev is not None:
                _exp = _prev + r["in"] - r["out"]
                if abs(_exp - b) > max(1.0, abs(b) * 0.01):
                    _bad += 1
                    anomalies.append({"type": "余额不平",
                                      "reason": f"按前序推算余额应为 {_exp:,.2f}，实际 {b:,.2f}（差 {_exp - b:,.2f}），疑似漏记或割裂流水", **r})
            _prev = b
        # 不平比例过高说明并非连续流水，放弃标记以免误报
        if _bad > len(_bal_rows) * 0.3:
            anomalies = [a for a in anomalies if a["type"] != "余额不平"]

    # ---- 相近重复交易（同对手 + 同金额，批次内再现，疑似拆分/重复）----
    if len(rows) <= 6000:
        _cnt: dict = {}
        for r in rows:
            _amt = round(max(r["in"], r["out"]), 2)
            if _amt <= 0 or not r["counterparty"]:
                continue
            _k = (r["counterparty"], _amt)
            _cnt[_k] = _cnt.get(_k, 0) + 1
        _repeat = {k for k, v in _cnt.items() if v >= 2}
        for r in rows:
            _amt = round(max(r["in"], r["out"]), 2)
            if _amt > 0 and r["counterparty"] and (r["counterparty"], _amt) in _repeat:
                anomalies.append({"type": "相近重复",
                                  "reason": f"本批次内与「{r['counterparty']}」同额 {_amt:,.2f} 交易出现 ≥2 次，疑似拆分或重复入账", **r})

    monthly = defaultdict(lambda: {"in": 0.0, "out": 0.0, "n": 0})
    for r in rows:
        mkey = r["date"][:7]
        monthly[mkey]["in"] += r["in"]
        monthly[mkey]["out"] += r["out"]
        monthly[mkey]["n"] += 1
    return {
        "threshold": threshold,
        "total_in": round(total_in, 2), "total_out": round(total_out, 2),
        "net": round(total_in - total_out, 2),
        "n_rows": len(rows), "n_anomalies": len(anomalies),
        "anomalies": anomalies[:800],
        "risky_counterparties": sorted(risky_cps, key=lambda x: -x["total"])[:20],
        "fund_return_counterparties": sorted(_fund_return)[:20],
        "monthly": [{"month": k, **{kk: round(vv, 2) for kk, vv in v.items()}}
                    for k, v in sorted(monthly.items())],
        "disclaimer": "以上为规则筛查提示，不构成定性结论，须由办案人员结合证据人工研判",
        "analyzed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


# ---------------- 解析（不落库，返回预览+建议映射+质检预览）----------------
@router.post("/parse")
async def parse(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = await file.read()
    raw, enc = _read_raw(file.filename or "", data)
    if raw.empty or len(raw) < 2:
        raise HTTPException(422, "文件无有效数据行")
    header_idx, header_cells, template = _detect_header(raw)
    mapping, pending = _suggest_mapping(header_cells)
    # 关键词未命中关键字段 → 尝试 LLM 推断（离线自动跳过）
    if pending:
        llm_map = _llm_mapping(header_cells, [_cells(raw.iloc[i]) for i in range(header_idx + 1, min(header_idx + 4, len(raw)))])
        for k, v in llm_map.items():
            if k not in mapping:
                mapping[k] = v
        _, pending = _suggest_mapping(header_cells)  # 重新计算 pending
    # 质检预览（用建议映射，全量，但不落库）
    rows_preview, qc = _normalize(raw, header_idx, mapping, DEFAULT_THRESHOLD)
    sample = []
    for i in range(header_idx + 1, min(header_idx + 9, len(raw))):
        cells = _cells(raw.iloc[i])
        sample.append({header_cells[j]: cells[j] for j in range(len(header_cells)) if j < len(cells)})
    return {
        "filename": file.filename or "", "encoding": enc, "header_row": header_idx,
        "template": template, "columns": header_cells, "sample": sample,
        "suggested": mapping, "pending": pending, "qc": qc,
        "preview_rows": min(50, len(rows_preview)),
    }


# ---------------- 确认导入（带选定映射落库 + 完整性质检 + 分析）----------------
@router.post("/confirm")
async def confirm(
    file: UploadFile = File(...),
    mapping: str = Form("{}"),
    header_row: int = Form(0),
    threshold: float = Form(DEFAULT_THRESHOLD),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    import json
    try:
        mp = json.loads(mapping) if mapping else {}
    except Exception:
        mp = {}
    if not isinstance(mp, dict) or not mp:
        raise HTTPException(422, "未提供有效的列映射")
    data = await file.read()
    raw, _enc = _read_raw(file.filename or "", data)
    if raw.empty or len(raw) <= header_row:
        raise HTTPException(422, "文件无有效数据行或表头行越界")
    rows, qc = _normalize(raw, header_row, mp, threshold)
    if not rows:
        raise HTTPException(422, "清洗后无有效交易行，请检查列映射与文件格式")
    analysis = analyze(rows, threshold)
    s = BankStatement(
        filename=file.filename or "", row_count=len(rows), dup_count=qc["dup_rows"],
        total_in=analysis["total_in"], total_out=analysis["total_out"],
        cleaned=rows, analysis=analysis,
        source_rows=qc["source_rows"], parsed_rows=qc["parsed_rows"], skipped_rows=qc["skipped_rows"],
        missing_report=qc["missing_report"], completeness_score=qc["score"],
        completeness_level=qc["level"], header_row=header_row, mapping=mp, template=qc.get("template", "") or "",
        created_by=user.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    write_log(db, user_id=user.id, username=user.username, action="bank_import",
              resource_type="bank_statement", resource_id=s.id,
              detail={"rows": len(rows), "dup": qc["dup_rows"], "completeness": qc["level"],
                      "score": qc["score"], "anomalies": analysis["n_anomalies"], "template": s.template})
    return {
        "id": s.id, "row_count": len(rows), "dup_count": qc["dup_rows"],
        "completeness": {"level": qc["level"], "score": qc["score"], "skipped": qc["skipped_rows"],
                         "guide": qc["guide"]},
        "analysis": {k: analysis[k] for k in ("total_in", "total_out", "net", "n_rows",
                                              "n_anomalies", "analyzed_at")},
    }


# ---------------- 列表 / 详情 / 删除 ----------------
def _stmt_out(s: BankStatement, creator: str = "", with_detail: bool = False) -> dict:
    d = {
        "id": s.id, "filename": s.filename, "account_name": s.account_name,
        "row_count": s.row_count, "dup_count": s.dup_count,
        "total_in": s.total_in, "total_out": s.total_out,
        "completeness_level": s.completeness_level, "completeness_score": s.completeness_score,
        "template": s.template, "header_row": s.header_row,
        "missing_report": s.missing_report, "mapping": s.mapping,
        "created_by": creator,
        "created_at": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "",
    }
    if with_detail:
        d["cleaned"] = s.cleaned
        d["analysis"] = s.analysis
        d["source_rows"] = s.source_rows
        d["parsed_rows"] = s.parsed_rows
        d["skipped_rows"] = s.skipped_rows
    return d


@router.get("")
def list_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(BankStatement).order_by(BankStatement.id.desc()).all()
    uids = {s.created_by for s in rows if s.created_by}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    return [_stmt_out(s, (users.get(s.created_by).real_name if s.created_by in users else "")) for s in rows]


@router.get("/{sid}")
def get_one(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = db.get(BankStatement, sid)
    if not s:
        raise HTTPException(404, "流水批次不存在")
    c = db.get(User, s.created_by) if s.created_by else None
    return _stmt_out(s, c.real_name if c else "", with_detail=True)


@router.get("/{sid}/quality")
def quality(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """单批次完整度诊断（命门闭环）：逐字段缺失率 + 缺失记录示例 + 账目核对 + 修复建议。
    全部由已落库字段重建（missing_report/cleaned/source_parsed_skipped/dup/mapping），零迁移、不落库。"""
    s = db.get(BankStatement, sid)
    if not s:
        raise HTTPException(404, "流水批次不存在")
    rows = s.cleaned or []
    mr = s.missing_report or {}
    fields = []
    for f, v in mr.items():
        rate = float(v.get("rate", 0) or 0)
        sev = "high" if rate >= 0.4 else ("mid" if rate >= 0.15 else "low")
        fields.append({"std": f, "label": STD_LABELS.get(f, f), "present": v.get("present", 0),
                       "missing": v.get("missing", 0), "rate": rate, "severity": sev})
    fields.sort(key=lambda x: -x["rate"])
    gap_labels = [("counterparty", "对方户名"), ("summary", "摘要"), ("account", "对方账号"),
                  ("balance", "余额"), ("time", "交易时间")]
    gap = []
    for r in rows:
        missing = [lbl for std, lbl in gap_labels if not str(r.get(std, "") or "").strip()]
        if missing:
            gap.append({
                "date": r.get("date", ""), "time": r.get("time", ""), "summary": r.get("summary", ""),
                "counterparty": r.get("counterparty", ""), "account": r.get("account", ""),
                "balance": r.get("balance", ""), "amount": r.get("amount", 0),
                "in": r.get("in", 0), "out": r.get("out", 0), "missing_fields": missing,
            })
        if len(gap) >= 300:
            break
    dates = [r["date"] for r in rows if r.get("date")]
    date_range = [min(dates), max(dates)] if dates else ["", ""]
    high_fields = [f["label"] for f in fields if f["severity"] == "high"]
    suggestions = []
    if high_fields:
        suggestions.append(f"以下字段缺失率≥40%：{'、'.join(high_fields)}。优先核对源列是否映射遗漏；若原始数据本就空缺，需向相关方补充后再导入。")
    if (s.skipped_rows or 0) > 0:
        suggestions.append(f"清洗跳行 {s.skipped_rows} 条（原始 {s.source_rows or 0} 行），多为日期无法解析/零金额/空行；建议核对银行导出格式或金额列。")
    if (s.dup_count or 0) > 0:
        suggestions.append(f"剔除重复 {s.dup_count} 条（同日期+金额+对手+摘要判重）；如确为多笔相同交易，请在原始流水中补充区分信息。")
    if s.completeness_level in ("低", "中") and not high_fields:
        suggestions.append("完整度中等偏低、无单一高危字段，多为多字段轻微缺失；补齐下方缺失清单后重新导入可提升完整度。")
    if not suggestions:
        suggestions.append("数据完整度良好，可直接用于分析。")
    return {
        "id": s.id, "filename": s.filename, "level": s.completeness_level, "score": s.completeness_score,
        "template": s.template, "header_row": s.header_row, "mapping": s.mapping or {},
        "accounting": {"source_rows": s.source_rows or 0, "parsed_rows": s.parsed_rows or s.row_count,
                       "skipped_rows": s.skipped_rows or 0, "dup_rows": s.dup_count or 0},
        "date_range": date_range, "fields": fields, "gap_records": gap, "suggestions": suggestions,
    }


@router.delete("/{sid}")
def remove(sid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = db.get(BankStatement, sid)
    if not s:
        raise HTTPException(404, "流水批次不存在")
    db.delete(s)
    db.commit()
    write_log(db, user_id=user.id, username=user.username, action="bank_delete",
              resource_type="bank_statement", resource_id=sid)
    return {"ok": True}
