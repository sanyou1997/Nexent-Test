"""认证路由：登录 / 当前用户 / 登出。

登录频率限制：按用户名 + 客户端 IP 维度计数，5 次失败后锁定 5 分钟，
            锁定期间任何尝试都直接 429 拒绝（避免攻击者通过快速尝试枚举密码）。
"""
import time
from collections import defaultdict
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from app import audit, tenancy
from app.database import get_db
from app.deps import get_current_user
from app.models import Department, User
from app.schemas import LoginIn
from app.security import verify_password, create_access_token

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 失败计数：{(username, ip): [fail_count, lock_until_ts]}
# 内存版仅适用于单进程；多进程部署需换 Redis 等共享存储
_failures: dict[tuple[str, str], list] = defaultdict(lambda: [0, 0.0])
_FAIL_LIMIT = 5
_LOCK_SECONDS = 5 * 60


def _client_ip(req: Request) -> str:
    # 反代场景下取 X-Forwarded-For 首段；直连取 client.host
    xff = req.headers.get("x-forwarded-for", "")
    return (xff.split(",")[0].strip() if xff else "") or (req.client.host if req.client else "unknown")


@router.post("/login")
def login(data: LoginIn, req: Request, db: Session = Depends(get_db)):
    key = (data.username, _client_ip(req))
    state = _failures[key]
    now = time.time()
    if state[1] > now:
        wait = int(state[1] - now)
        raise HTTPException(429, f"登录尝试过于频繁，请 {wait} 秒后再试")

    user = db.query(User).filter(User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        state[0] += 1
        if state[0] >= _FAIL_LIMIT:
            state[1] = now + _LOCK_SECONDS
            raise HTTPException(429, "登录失败次数过多，已锁定 5 分钟")
        raise HTTPException(401, f"用户名或密码错误（剩余 {_FAIL_LIMIT - state[0]} 次）")
    if not user.is_active:
        raise HTTPException(403, "账号已停用")
    scope = tenancy.build_scope(db, user)   # 所属部门停用时拒绝登录（403）
    # 登录成功，清空失败计数
    _failures.pop(key, None)
    audit.write_log(db, user.id, user.username, action="login")
    return {"access_token": create_access_token(user.id, user.role), "user": profile(db, user, scope)}


def profile(db: Session, user: User, scope: tenancy.Scope) -> dict:
    """当前账号 + 部门权限：前端据此生成导航（modules 为 null 表示全部模块）。"""
    dept = db.get(Department, user.dept_id) if user.dept_id else None
    return {
        "id": user.id, "username": user.username, "real_name": user.real_name, "role": user.role,
        "dept_id": user.dept_id, "dept_name": dept.name if dept else "",
        "modules": None if scope.modules is None else sorted(scope.modules),
        "data_scope": "all" if scope.dept_ids is None else (dept.data_scope if dept else "self"),
        "data_scope_label": "全部部门" if scope.dept_ids is None else
        tenancy.DATA_SCOPES.get(dept.data_scope if dept else "self", "本部门"),
    }


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return profile(db, user, tenancy.current_scope(db))


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    audit.write_log(db, user.id, user.username, action="logout")
    return {"ok": True}
