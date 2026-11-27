"""混合检索：本地稀疏 TF-IDF 余弦（默认）+ 云端稠密向量（可选），二者同权融合。
最高分低于拒答阈值 → 返回 refused 标记（对应 V2.0 设计：无依据不回答）。

性能：条款级语料在进程内缓存（按数量+最大 id 作 key），新增/删除条款自动失效，
QA 不再每次全表加载 JSON 向量列、不再每条 db.get(Regulation)。
"""
import math
import threading
from collections import Counter
from sqlalchemy.orm import Session
from app.config import settings
from app.models import RegulationArticle, Regulation
from app.llm import embeddings

_cache_lock = threading.Lock()
_cache: dict = {"key": None, "rows": None, "idf": None, "reg_map": None}


def _idf(rows: list[dict]) -> dict:
    n = len(rows) or 1
    df = Counter()
    for r in rows:
        tv = r.get("tf_vector")
        if tv:
            for t in tv:
                df[t] += 1
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def invalidate_corpus() -> None:
    """语料变动（增/删/改条款）后调用，使下一次检索重建缓存。"""
    with _cache_lock:
        _cache.update(key=None, rows=None, idf=None, reg_map=None)


def _load_corpus(db: Session):
    """加载全部条款 + 计算 IDF + 法规名映射，按 (count, max_id) 缓存。

    缓存的是纯 dict 数据（脱离 session 也可安全访问），不缓存 ORM 对象，
    避免下个请求 session 关闭后访问 r.tf_vector 触发 DetachedInstanceError。
    """
    # 只检索「现行有效」的法规：已废止、历史文本、未核实的语料不作为问答依据
    regs = db.query(Regulation.id, Regulation.title, Regulation.status).all()
    live = {r.id: r.title for r in regs if r.status == "现行有效"}
    rows = [r for r in db.query(RegulationArticle).all() if r.regulation_id in live]
    # 缓存键含各法规状态：在别的进程里改了效力状态，这里也能感知
    key = (len(rows), max((r.id for r in rows), default=0), hash(tuple(sorted((r.id, r.status) for r in regs))))
    with _cache_lock:
        if _cache["key"] != key:
            reg_map = live
            # 转为纯 dict 快照，切断与 session 的绑定
            snapshot = [{
                "id": r.id, "regulation_id": r.regulation_id,
                "chapter": r.chapter or "", "article_no": r.article_no,
                "content": r.content,
                "tf_vector": r.tf_vector or {},
                "dense_vector": r.dense_vector,
            } for r in rows]
            _cache.update(key=key, rows=snapshot, idf=_idf(snapshot), reg_map=reg_map)
    return _cache["rows"], _cache["idf"], _cache["reg_map"]


def retrieve(db: Session, query: str, top_k: int | None = None) -> dict:
    top_k = top_k or settings.RETRIEVE_TOP_K
    rows, idf, reg_map = _load_corpus(db)
    if not rows:
        return {"mode": "empty", "hits": []}
    q_sparse = embeddings.embed_local(query)
    q_dense = embeddings.embed_dense([query])
    q_dense = q_dense[0] if q_dense else None

    scored = []
    for r in rows:
        s = embeddings.cosine_sparse(q_sparse, r.get("tf_vector") or {}, idf)
        if q_dense is not None and r.get("dense_vector"):
            s = 0.5 * s + 0.5 * embeddings.cosine_dense(q_dense, r["dense_vector"])
        scored.append({
            "article_id": r["id"], "regulation": reg_map.get(r["regulation_id"], ""),
            "chapter": r["chapter"], "article_no": r["article_no"],
            "content": r["content"], "score": round(s, 4),
        })
    scored.sort(key=lambda x: -x["score"])
    hits = scored[:top_k]
    refused = (not hits) or hits[0]["score"] < settings.REFUSE_THRESHOLD
    return {"mode": "refused" if refused else "ok", "hits": hits}
