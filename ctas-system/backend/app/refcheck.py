"""历史报告的法规引用校验：报告里写的「《X法》第N条」是否真实存在。

为什么要校验：报告里援引的条款如果法规库中不存在（错条/写错法名/已废止），
办案人员照着引用会出事。校验结果并入质检报告，缺失可见。

同时提供「法规依据回填」：把报告引用映射到库内条款 id，供类案参考时直接跳转。
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from app.models import Regulation, RegulationArticle
from app.rag.ingest import cn2num

_RE_REF = re.compile(r"《(?P<law>[^《》]{2,40})》\s*第\s*(?P<no>[〇零一二三四五六七八九十百千\d]{1,10})\s*条")
# 副标题/括注：法规全称里常有「（试行）」「（2018年修订）」，比对时去掉
_PAREN = re.compile(r"[（(][^（）()]{0,20}[)）]")


def _norm_law(title: str) -> str:
    t = _PAREN.sub("", title or "").strip()
    return re.sub(r"\s+", "", t)


def _norm_no(no: str) -> int:
    s = (no or "").strip()
    if s.isdigit():
        return int(s)
    return cn2num(s)


def _law_index(db: Session) -> dict[str, list[tuple[int, str]]]:
    """{归一化法规名: [(article_id, article_no)]}，同时含全称与去括注基名。"""
    idx: dict[str, list[tuple[int, str]]] = {}
    regs = db.query(Regulation).all()
    for reg in regs:
        full = _norm_law(reg.title)
        base = full
        arts = db.query(RegulationArticle).filter(
            RegulationArticle.regulation_id == reg.id).all()
        pairs = [(a.id, a.article_no or "") for a in arts]
        if full:
            idx.setdefault(full, []).extend(pairs)
        if base and base != full:
            idx.setdefault(base, []).extend(pairs)
    return idx


def check_refs(db: Session, refs: list[dict]) -> list[dict]:
    """校验一组引用 [{law, article_no}]。

    返回 [{law, article_no, status, matched_article_id, matched_no, reason}]
    status 三态：
      - ok       法规与条款在库中均存在
      - missing  法规在库中不存在（属于"未入库"，不算错误，只是提醒可补录）
      - bad      法规存在但该条号不存在（真正的引用错误，必须人工改）
    """
    if not refs:
        return []
    idx = _law_index(db)
    out: list[dict] = []
    for r in refs:
        law = (r.get("law") or "").strip()
        no_raw = (r.get("article_no") or "").strip()
        want = _norm_no(no_raw)
        item = {"law": law, "article_no": no_raw, "status": "missing",
                "ok": False, "matched_article_id": None, "matched_no": "", "reason": ""}
        key = _norm_law(law)
        cands = idx.get(key)
        if not cands:
            # 退一步：模糊匹配（法规名互相包含，如"监察法" vs "监察法实施条例"）
            for k, v in idx.items():
                if k and (k in key or key in k):
                    cands = v
                    break
        if not cands:
            item["reason"] = "该法规尚未收录到法规库（可补录后自动校验）"
            out.append(item)
            continue
        # 条号匹配：库内条号可能是「第一百零一条」形式
        matched = None
        for aid, ano in cands:
            if _norm_no(ano) == want:
                matched = (aid, ano)
                break
        if matched:
            item.update(ok=True, status="ok", matched_article_id=matched[0], matched_no=matched[1])
        else:
            avail = sorted({_norm_no(a) for _, a in cands if _norm_no(a)})
            near = [n for n in avail if abs(n - want) <= 1] if want else []
            # 调用方可能传「二百零八」也可能传「第二百零八条」，展示时统一，避免出现「第第…条条」
            label = re.sub(r"^第\s*|\s*条$", "", no_raw)
            item["status"] = "bad"
            item["reason"] = (f"该法规无第{label}条（相邻条款：第{near[0]}条）" if near
                              else f"该法规库内无第{label}条" + (f"（库存版本共 {avail[-1]} 条）"
                                                              if avail and want and want > avail[-1] else ""))
        out.append(item)
    return out


def extract_refs(text: str) -> list[dict]:
    """从文本抽取去重后的法规引用。"""
    refs, seen = [], set()
    for m in _RE_REF.finditer(text or ""):
        law = m.group("law").strip()
        no = m.group("no").strip()
        key = (_norm_law(law), _norm_no(no))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        refs.append({"law": law, "article_no": f"第{no}条"})
        if len(refs) >= 50:
            break
    return refs
