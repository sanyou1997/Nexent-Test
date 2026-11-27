"""部门与权限：部门（租户）维护、功能模块、数据范围、移交承办部门、部门级 MCP 令牌。

平台管理员维护；业务账号只能取「承办部门候选」（移交时选择接收部门）。规则说明见 app/tenancy.py。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, tenancy
from app.database import get_db
from app.deps import ROLE_NAMES, get_current_user, require_roles
from app.models import (
    Alert, Archive, Case, ConductCase, DataGrant, Department, Document, Inspection, Petition, Transcript, Trial,
    User,
)

router = APIRouter(prefix="/api/departments", tags=["departments"])

# 部门数据概览 / 范围预览统计的主要业务表
SUMMARY = [("petitions", "信访件", Petition), ("cases", "线索 / 案件", Case), ("trials", "审理事项", Trial),
           ("conduct", "党风问题", ConductCase), ("inspections", "巡察", Inspection), ("alerts", "预警", Alert),
           ("documents", "文书", Document), ("archives", "档案", Archive), ("transcripts", "谈话录音", Transcript)]


class DeptIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=30)
    name: str = Field(..., min_length=1, max_length=80)
    description: str = ""
    modules: list[str] = []
    data_scope: str = "self"
    scope_dept_ids: list[int] = []
    enabled: bool = True
    sort: int = 0


class ScopePreviewIn(BaseModel):
    dept_id: int | None = None
    data_scope: str = "self"
    scope_dept_ids: list[int] = []


def _clean(db: Session, data: DeptIn, dept_id: int | None) -> dict:
    code = data.code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9_-]{1,30}", code):
        raise HTTPException(400, "部门编码只能是字母、数字、下划线或短横线")
    name = data.name.strip()
    for col, val, label in ((Department.code, code, "编码"), (Department.name, name, "名称")):
        hit = db.query(Department).filter(col == val).first()
        if hit and hit.id != dept_id:
            raise HTTPException(400, f"部门{label}已存在")
    if data.data_scope not in tenancy.DATA_SCOPES:
        raise HTTPException(400, "数据范围应为 本部门 / 本部门 + 指定部门 / 全部部门")
    unknown = [m for m in data.modules if m not in tenancy.ASSIGNABLE]
    if unknown:
        raise HTTPException(400, f"未知或不可分配的模块：{'、'.join(unknown)}")
    others = []
    if data.data_scope == "custom":
        valid = {d.id for d in db.query(Department).all()}
        others = sorted({i for i in data.scope_dept_ids if i in valid and i != dept_id})
        if not others:
            raise HTTPException(400, "「本部门 + 指定部门」须至少选择一个其他部门")
    mods = [m for m in tenancy.ASSIGNABLE if m in set(data.modules)]   # 按目录顺序去重
    return {"code": code, "name": name, "description": data.description.strip()[:300], "modules": mods,
            "data_scope": data.data_scope, "scope_dept_ids": others, "enabled": data.enabled, "sort": data.sort}


def _counts(db: Session) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for key, _label, cls in SUMMARY:
        rows = db.execute(select(cls.dept_id, func.count(cls.id)).group_by(cls.dept_id)
                          .execution_options(**tenancy.BYPASS)).all()
        for did, n in rows:
            if did is not None:
                out.setdefault(did, {})[key] = n
    return out


def _out(d: Department, members: int, counts: dict, names: dict[int, str]) -> dict:
    return {
        "id": d.id, "code": d.code, "name": d.name, "description": d.description or "",
        "modules": [m for m in (d.modules or []) if m in tenancy.ASSIGNABLE],
        "data_scope": d.data_scope, "data_scope_label": tenancy.DATA_SCOPES.get(d.data_scope, d.data_scope),
        "scope_dept_ids": d.scope_dept_ids or [],
        "scope_dept_names": [names[i] for i in (d.scope_dept_ids or []) if i in names],
        "is_system": bool(d.is_system), "enabled": bool(d.enabled), "sort": d.sort or 0,
        "members": members, "data": counts, "mcp_token": bool(d.mcp_token_hash),
        "updated_at": d.updated_at.strftime("%Y-%m-%d %H:%M") if d.updated_at else "",
    }


@router.get("/catalog")
def catalog(user: User = Depends(get_current_user)):
    """模块目录 / 数据范围选项 / 角色（前端导航与配置界面共用）。"""
    return {"modules": tenancy.MODULES, "assignable": tenancy.ASSIGNABLE, "data_scopes": tenancy.DATA_SCOPES,
            "roles": ROLE_NAMES, "summary": [{"key": k, "label": label} for k, label, _ in SUMMARY]}


@router.get("/receivers")
def receivers(modules: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """移交时可选的承办部门（已启用、开通了目标模块）；default 为不选时系统自动确定的部门（多个候选时为空，须选择）。"""
    mods = [m for m in modules.split(",") if m in tenancy.MODULE_LABEL]
    if not mods:
        raise HTTPException(400, "modules 无效")
    cands = tenancy.receivers(db, mods)
    try:
        default = tenancy.route_to(db, user, mods)
    except HTTPException:
        default = None
    return {"items": [{"id": d.id, "name": d.name} for d in cands], "default": default}


@router.get("")
def list_departments(db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    depts = db.query(Department).order_by(Department.sort, Department.id).all()
    names = {d.id: d.name for d in depts}
    members = dict(db.query(User.dept_id, func.count(User.id)).group_by(User.dept_id).all())
    counts = _counts(db)
    grants = db.query(func.count(DataGrant.id)).scalar() or 0
    return {"items": [_out(d, members.get(d.id, 0), counts.get(d.id, {}), names) for d in depts],
            "grants": grants}


@router.post("")
def create_department(data: DeptIn, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    vals = _clean(db, data, None)
    d = Department(**vals)
    db.add(d)
    db.commit()
    audit.write_log(db, user.id, user.username, action="dept_create", resource_type="department",
                    resource_id=d.id, detail={"name": d.name, "modules": d.modules, "data_scope": d.data_scope,
                                              "scope_dept_ids": d.scope_dept_ids})
    return _out(d, 0, {}, {x.id: x.name for x in db.query(Department).all()})


@router.put("/{did}")
def update_department(did: int, data: DeptIn, db: Session = Depends(get_db),
                      user: User = Depends(require_roles("admin"))):
    d = db.get(Department, did)
    if not d:
        raise HTTPException(404, "部门不存在")
    vals = _clean(db, data, did)
    if d.is_system and not vals["enabled"]:
        raise HTTPException(400, "系统管理部门不可停用")
    before = {"modules": d.modules, "data_scope": d.data_scope, "scope_dept_ids": d.scope_dept_ids,
              "enabled": d.enabled, "name": d.name}
    for k, v in vals.items():
        setattr(d, k, v)
    tenancy.touch(d)
    db.commit()
    after = {k: getattr(d, k) for k in before}
    audit.write_log(db, user.id, user.username, action="dept_update", resource_type="department",
                    resource_id=did, detail={"before": before, "after": after})
    names = {x.id: x.name for x in db.query(Department).all()}
    members = db.query(func.count(User.id)).filter(User.dept_id == did).scalar() or 0
    return _out(d, members, _counts(db).get(did, {}), names)


@router.delete("/{did}")
def delete_department(did: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    d = db.get(Department, did)
    if not d:
        raise HTTPException(404, "部门不存在")
    if d.is_system:
        raise HTTPException(400, "系统管理部门不可删除")
    if db.query(User).filter(User.dept_id == did).count():
        raise HTTPException(400, "部门下仍有账号，请先调整账号所属部门（或改为停用部门）")
    owned = sum(_counts(db).get(did, {}).values())
    if owned:
        raise HTTPException(400, f"部门名下仍有 {owned} 条业务数据，不能删除，可改为停用")
    for other in db.query(Department).all():
        if did in (other.scope_dept_ids or []):
            other.scope_dept_ids = [i for i in other.scope_dept_ids if i != did]
    db.query(DataGrant).filter(DataGrant.dept_id == did).delete()
    db.delete(d)
    db.commit()
    audit.write_log(db, user.id, user.username, action="dept_delete", resource_type="department",
                    resource_id=did, detail={"name": d.name})
    return {"ok": True}


@router.post("/preview-scope")
def preview_scope(data: ScopePreviewIn, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    """按（未保存的）数据范围配置，预览该部门账号能看到多少业务数据（含移交授权的记录）。"""
    if data.data_scope not in tenancy.DATA_SCOPES:
        raise HTTPException(400, "数据范围无效")
    if data.data_scope == "all":
        ids = None
    else:
        ids = tuple(dict.fromkeys([i for i in [data.dept_id, *(data.scope_dept_ids if data.data_scope == "custom" else [])]
                                   if i]))
    probe = tenancy.Scope(None, data.dept_id, ids, None, "preview")
    saved = tenancy.current_scope(db)
    tenancy.apply_scope(db, probe)
    try:
        counts = {key: db.scalar(select(func.count(cls.id))) or 0 for key, _l, cls in SUMMARY}
    finally:
        tenancy.apply_scope(db, saved)
    return {"counts": counts}


@router.post("/{did}/mcp-token")
def issue_mcp_token(did: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    """生成部门级 MCP 服务令牌（只显示一次）：在该部门对应的 Nexent 租户注册 ctas-tools 时填写，
    智能体经 MCP 只能查到该部门数据范围内的业务数据。重新生成即作废旧令牌。"""
    d = db.get(Department, did)
    if not d:
        raise HTTPException(404, "部门不存在")
    token = tenancy.new_token()
    d.mcp_token_hash = tenancy.hash_token(token)
    tenancy.touch(d)
    db.commit()
    audit.write_log(db, user.id, user.username, action="dept_mcp_token", resource_type="department",
                    resource_id=did, detail={"op": "issue"})
    return {"token": token}


@router.delete("/{did}/mcp-token")
def revoke_mcp_token(did: int, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    d = db.get(Department, did)
    if not d:
        raise HTTPException(404, "部门不存在")
    d.mcp_token_hash = ""
    tenancy.touch(d)
    db.commit()
    audit.write_log(db, user.id, user.username, action="dept_mcp_token", resource_type="department",
                    resource_id=did, detail={"op": "revoke"})
    return {"ok": True}


@router.get("/grants")
def list_grants(limit: int = 100, db: Session = Depends(get_db), user: User = Depends(require_roles("admin"))):
    """移交授权清单（哪条记录因移交对哪个部门可见）。"""
    names = {d.id: d.name for d in db.query(Department).all()}
    rows = db.query(DataGrant).order_by(DataGrant.id.desc()).limit(max(1, min(limit, 500))).all()
    label = {"petitions": "信访件", "cases": "线索 / 案件", "trials": "审理事项", "conduct_cases": "党风问题"}
    return {"items": [{"id": g.id, "resource": label.get(g.resource_table, g.resource_table),
                       "resource_table": g.resource_table, "resource_id": g.resource_id,
                       "dept_id": g.dept_id, "dept_name": names.get(g.dept_id, ""), "reason": g.reason,
                       "created_at": g.created_at.strftime("%Y-%m-%d %H:%M") if g.created_at else ""}
                      for g in rows]}

