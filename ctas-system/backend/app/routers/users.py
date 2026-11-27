"""账号管理：账号归属部门（租户）+ 岗位角色。部门决定可见模块与数据范围，角色决定模块内可办理的操作。"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app import audit
from app.database import get_db
from app.deps import require_roles, ROLE_NAMES
from app.models import Department, User
from app.schemas import UserCreate
from app.security import hash_password

router = APIRouter(prefix="/api/users", tags=["users"])


class UserUpdate(BaseModel):
    real_name: str | None = None
    role: str | None = None
    dept_id: int | None = None


class PasswordReset(BaseModel):
    password: str = Field(..., min_length=8, max_length=64)


def _dept(db: Session, dept_id: int | None) -> Department:
    d = db.get(Department, dept_id) if dept_id else None
    if not d:
        raise HTTPException(400, "请选择所属部门")
    if not d.enabled:
        raise HTTPException(400, "该部门已停用")
    return d


def _out(u: User, names: dict[int, str]) -> dict:
    return {"id": u.id, "username": u.username, "real_name": u.real_name,
            "role": u.role, "role_name": ROLE_NAMES.get(u.role, u.role),
            "dept_id": u.dept_id, "dept_name": names.get(u.dept_id or 0, ""),
            "is_active": u.is_active,
            "created_at": u.created_at.strftime("%Y-%m-%d") if u.created_at else ""}


def _names(db: Session) -> dict[int, str]:
    return {d.id: d.name for d in db.query(Department).all()}


@router.get("")
def list_users(dept_id: int | None = None, db: Session = Depends(get_db),
               user: User = Depends(require_roles("admin"))):
    q = db.query(User)
    if dept_id:
        q = q.filter(User.dept_id == dept_id)
    names = _names(db)
    return [_out(u, names) for u in q.order_by(User.dept_id, User.id).all()]


@router.post("")
def create_user(data: UserCreate, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    if data.role not in ROLE_NAMES:
        raise HTTPException(400, f"角色无效，可选：{'/'.join(ROLE_NAMES)}")
    if len(data.password or "") < 8:
        raise HTTPException(400, "初始密码至少 8 位")
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(400, "用户名已存在")
    d = _dept(db, data.dept_id)
    u = User(username=data.username, password_hash=hash_password(data.password),
             real_name=data.real_name, role=data.role, dept_id=d.id)
    db.add(u)
    db.commit()
    audit.write_log(db, user.id, user.username, action="user_create",
                    resource_type="user", resource_id=u.id,
                    detail={"new_user": data.username, "dept": d.name, "role": data.role})
    return _out(u, _names(db))


@router.put("/{uid}")
def update_user(uid: int, data: UserUpdate, db: Session = Depends(get_db),
                user: User = Depends(require_roles("admin"))):
    target = db.get(User, uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    before = {"real_name": target.real_name, "role": target.role, "dept_id": target.dept_id}
    if data.role is not None:
        if data.role not in ROLE_NAMES:
            raise HTTPException(400, "角色无效")
        if target.id == user.id and data.role != "admin":
            raise HTTPException(400, "不能取消自己的管理员角色")
        target.role = data.role
    if data.dept_id is not None:
        target.dept_id = _dept(db, data.dept_id).id
    if data.real_name is not None:
        target.real_name = data.real_name.strip()[:50]
    db.commit()
    audit.write_log(db, user.id, user.username, action="user_update", resource_type="user", resource_id=uid,
                    detail={"before": before, "after": {"real_name": target.real_name, "role": target.role,
                                                        "dept_id": target.dept_id}})
    return _out(target, _names(db))


@router.put("/{uid}/toggle")
def toggle_user(uid: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    target = db.get(User, uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.id == user.id:
        raise HTTPException(400, "不能停用自己")
    target.is_active = not target.is_active
    db.commit()
    audit.write_log(db, user.id, user.username, action="user_toggle",
                    resource_type="user", resource_id=uid, detail={"active": target.is_active})
    return {"ok": True, "is_active": target.is_active}


@router.post("/{uid}/password")
def reset_password(uid: int, data: PasswordReset, db: Session = Depends(get_db),
                   user: User = Depends(require_roles("admin"))):
    target = db.get(User, uid)
    if not target:
        raise HTTPException(404, "用户不存在")
    target.password_hash = hash_password(data.password)
    db.commit()
    audit.write_log(db, user.id, user.username, action="user_reset_password", resource_type="user",
                    resource_id=uid, detail={"username": target.username})
    return {"ok": True}
