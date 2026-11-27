from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app import audit
from app.database import get_db
from app.deps import get_current_user
from app.models import User

router = APIRouter(prefix="/api/audit", tags=["审计"])

@router.get("")
def list_logs(page: int = 1, per_page: int = 20, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    total = db.query(audit.AuditLog).count()
    page = max(1, page)
    per_page = min(max(1, per_page), 200)
    logs = (db.query(audit.AuditLog).order_by(audit.AuditLog.id.desc())
            .offset((page - 1) * per_page).limit(per_page).all())
    items = [{
        "id": lg.id, "username": lg.username, "action": lg.action,
        "resource_type": lg.resource_type, "resource_id": lg.resource_id,
        "detail": lg.detail,
        "created_at": lg.created_at.strftime("%Y-%m-%d %H:%M:%S") if lg.created_at else "",
        "hash": lg.hash[:16],
    } for lg in logs]
    return {"items": items, "total": total}

@router.get("/verify")
def verify(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return audit.verify_chain(db)
