"""把当前（已核对）的法规库导出为演示夹具 app/fixtures/regulations.json（向量由加载器重算，不入夹具）。"""
import json
import sys

from app.database import SessionLocal
from app.models import Regulation, RegulationArticle

db = SessionLocal()
regs, arts = [], []
for r in db.query(Regulation).order_by(Regulation.id).all():
    regs.append({"id": r.id, "title": r.title, "version": r.version, "effective_date": r.effective_date,
                 "status": r.status, "source": r.source, "is_official": bool(r.is_official), "category": r.category,
                 "required": bool(r.required), "created_at": r.created_at.isoformat() if r.created_at else None})
for a in db.query(RegulationArticle).order_by(RegulationArticle.regulation_id, RegulationArticle.id).all():
    arts.append({"id": a.id, "regulation_id": a.regulation_id, "chapter": a.chapter, "article_no": a.article_no,
                 "content": a.content, "dense_vector": None, "created_at": a.created_at.isoformat() if a.created_at else None})
out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/regulations.json"
json.dump({"regulations": regs, "regulation_articles": arts}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
print(len(regs), len(arts))
