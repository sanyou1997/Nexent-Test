"""审计日志：追加哈希链，任何删除/篡改会破坏链条可被校验发现。

并发安全：单进程内用 threading.Lock 串行化 prev_hash 读取 → 计算 → 插入；
        多进程部署应改用数据库行锁或唯一约束 + 重试，本演示版未覆盖。
"""
import hashlib
import json
import threading
from datetime import datetime
from sqlalchemy.orm import Session
from app.models import AuditLog

# 模块级锁，串行化"读上一条 hash → 写新条目"的关键段，避免并发写造成链条分叉
_write_lock = threading.Lock()


def _hash(prev_hash: str, payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(f"{prev_hash}|{raw}".encode("utf-8")).hexdigest()


def write_log(
    db: Session, user_id=None, username="anonymous", action="",
    resource_type="", resource_id="", detail=None,
):
    now = datetime.utcnow()
    payload = {
        "user_id": user_id, "username": username, "action": action,
        "resource_type": resource_type, "resource_id": str(resource_id),
        "detail": detail or {}, "ts": now.isoformat(),
    }
    with _write_lock:
        last = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        prev_hash = last.hash if last else ""
        log = AuditLog(
            user_id=user_id, username=username, action=action,
            resource_type=resource_type, resource_id=str(resource_id),
            detail=detail or {}, prev_hash=prev_hash,
            hash=_hash(prev_hash, payload), created_at=now,
        )
        db.add(log)
        db.commit()
    return log


def verify_chain(db: Session) -> dict:
    logs = db.query(AuditLog).order_by(AuditLog.id.asc()).all()
    prev = ""
    broken_at = None
    for lg in logs:
        expect_prev = prev
        payload = {
            "user_id": lg.user_id, "username": lg.username, "action": lg.action,
            "resource_type": lg.resource_type, "resource_id": lg.resource_id,
            "detail": lg.detail or {}, "ts": lg.created_at.isoformat() if lg.created_at else "",
        }
        if lg.prev_hash != expect_prev or lg.hash != _hash(expect_prev, payload):
            broken_at = lg.id
            break
        prev = lg.hash
    return {"valid": broken_at is None, "broken_at": broken_at, "total": len(logs)}
