from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app import tenancy
from app.database import get_db
from app.security import decode_token
from app.models import User

bearer = HTTPBearer(auto_error=False)

def get_current_user(
    request: Request,
    cred: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """当前用户 + 部门数据范围：范围挂到本请求的 Session 上（ORM 统一过滤），并按模块把关接口。"""
    if cred is None:
        raise HTTPException(401, "未登录")
    try:
        payload = decode_token(cred.credentials)
    except Exception:
        raise HTTPException(401, "登录已过期，请重新登录")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active:
        raise HTTPException(401, "用户不存在或已停用")
    scope = tenancy.build_scope(db, user)
    tenancy.apply_scope(db, scope)
    tenancy.check_api_access(request.url.path, user, scope)
    return user

ROLE_NAMES = {"admin": "平台管理员", "xinfang": "信访岗", "banan": "办案岗", "shenli": "审理岗"}  # 岗位角色（部门另设）

def require_roles(*roles):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, f"需要角色：{'/'.join(ROLE_NAMES.get(r, r) for r in roles)}")
        return user
    return checker
