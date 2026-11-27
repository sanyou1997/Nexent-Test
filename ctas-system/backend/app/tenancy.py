"""部门（租户）隔离：功能模块、数据范围、行级隔离、移交授权。

一、规则
- 每个账号归属一个部门；部门配置「功能模块」（看得到哪些工作面）与「数据范围」（看得到哪些部门的业务数据）。
- 数据范围：self 本部门 / custom 本部门 + 指定部门 / all 全部部门。平台管理员（admin 角色）不受限。
- 业务数据按部门归属（dept_id，新建时自动取经办人所在部门）：
  · DeptOwned / DeptGrantable：只对数据范围内的部门可见；
  · DeptOwnedOrShared：dept_id 为空为平台共享（内置监督模型），否则同上；
  · 其余表（法规库、文书模板、校验规则、历史报告库、审计日志…）全平台共享。
- 移交授权：信访转线索、移送审理、党风 / 巡察移交时，新记录归接收部门；接收部门可访问来源记录、
  来源部门可跟踪新记录（DataGrant，只对这一条记录生效），无需为此放开整个部门的数据范围。

二、实现
- 过滤在 ORM 层统一施加（SQLAlchemy do_orm_execute + with_loader_criteria），覆盖 query / get / count /
  批量 update / delete，各路由无需逐个改查询；新建记录在 flush 前自动归属部门（子表随父记录）。
- 请求的范围挂在 Session.info 上（deps.get_current_user 设置）；启动脚本、后台任务的 Session 不带范围（系统上下文）。
- 需要跨范围的系统操作（如生成全局唯一编号）显式加 execution_options(tenancy_bypass=True)。
- 接口按模块把关（check_api_access）：未开通模块的接口直接 403，不只是隐藏菜单。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session, with_loader_criteria

from app.models import (
    Alert, AlertFeedback, Appeal, Archive, BankStatement, Case, ConductCase, DataGrant, Department,
    DeptGrantable, DeptOwned, DeptOwnedOrShared, DispositionSuggestion, Document, DocumentVersion,
    Inspection, InspectionFinding, IntegrityReplyRecord, Interrogation, Petition, SuperviseModel, Trial,
    TrialMaterial, User,
)

SCOPE_KEY = "ctas.scope"
BYPASS = {"tenancy_bypass": True}

# ============================ 模块目录（前后端共用，前端从接口取） ============================
# kind：base 始终可用 / biz 业务模块（数据按部门隔离）/ shared 通用能力（数据全平台共享）/ system 仅平台管理员
MODULES: list[dict] = [
    {"key": "workbench", "label": "我的工作台", "group": "工作台", "kind": "base"},
    {"key": "petitions", "label": "信访受理", "group": "业务办理", "kind": "biz"},
    {"key": "clue", "label": "线索研判", "group": "业务办理", "kind": "biz"},
    {"key": "cases", "label": "案件管理", "group": "业务办理", "kind": "biz"},
    {"key": "trials", "label": "审理台账", "group": "业务办理", "kind": "biz"},
    {"key": "ctas-materials", "label": "送审材料", "group": "业务办理", "kind": "biz"},
    {"key": "conduct", "label": "党风政风", "group": "业务办理", "kind": "biz"},
    {"key": "inspections", "label": "巡察整改", "group": "业务办理", "kind": "biz"},
    {"key": "supervise", "label": "监督模型", "group": "监督预警", "kind": "biz"},
    {"key": "alerts", "label": "预警中枢", "group": "监督预警", "kind": "biz"},
    {"key": "bank", "label": "资金分析", "group": "监督预警", "kind": "biz"},
    {"key": "integrity", "label": "廉政档案", "group": "监督预警", "kind": "biz"},
    {"key": "ocr", "label": "材料识别", "group": "材料与文书", "kind": "biz"},
    {"key": "transcripts", "label": "谈话录音", "group": "材料与文书", "kind": "biz"},
    {"key": "archives", "label": "档案数字化", "group": "材料与文书", "kind": "biz"},
    {"key": "documents", "label": "文书库", "group": "材料与文书", "kind": "biz"},
    {"key": "verify", "label": "文书校验", "group": "材料与文书", "kind": "biz"},
    {"key": "agents", "label": "智能体中心", "group": "知识与智能", "kind": "shared"},
    {"key": "qa", "label": "法规问答", "group": "知识与智能", "kind": "shared"},
    {"key": "regulations", "label": "法规库", "group": "知识与智能", "kind": "shared"},
    {"key": "historical-reports", "label": "历史报告库", "group": "知识与智能", "kind": "shared"},
    {"key": "data-intake", "label": "数据接入", "group": "系统管理", "kind": "system"},
    {"key": "audit", "label": "审计日志", "group": "系统管理", "kind": "system"},
    {"key": "departments", "label": "部门与权限", "group": "系统管理", "kind": "system"},
    {"key": "users", "label": "用户管理", "group": "系统管理", "kind": "system"},
    {"key": "nexent-settings", "label": "Nexent 接入", "group": "系统管理", "kind": "system"},
    {"key": "recognition-settings", "label": "识别服务", "group": "系统管理", "kind": "system"},
]
MODULE_LABEL = {m["key"]: m["label"] for m in MODULES}
ASSIGNABLE = [m["key"] for m in MODULES if m["kind"] in ("biz", "shared")]
ALWAYS = frozenset(m["key"] for m in MODULES if m["kind"] == "base")
SHARED_KNOWLEDGE = [m["key"] for m in MODULES if m["kind"] == "shared"]
MATERIALS = ["ocr", "transcripts", "archives", "documents", "verify"]
# 线索承办：转线索时接收部门须开通「线索研判」（「案件管理」也用于审理室查阅移送案件，不作为线索承办依据）
CLUE_DESK = ("clue",)

DATA_SCOPES = {"self": "本部门", "custom": "本部门 + 指定部门", "all": "全部部门"}

# 接口前缀 → 可访问该接口的模块（具备任一即可）。未列出的接口为公共服务（文书、识别、智能体、法规、统计等），
# 其数据仍受行级隔离约束。页面跨模块调用（如审理页查案件、取类案）已按前端实际调用列入。
API_MODULES: dict[str, set[str]] = {
    "petitions": {"petitions", "cases", "clue"},
    "clue": {"clue"},
    "cases": {"cases", "clue", "trials", "ctas-materials"},
    "trials": {"trials", "ctas-materials"},
    "trial-materials": {"ctas-materials", "trials"},
    "appeals": {"trials"},
    "conduct": {"conduct"},
    "inspections": {"inspections"},
    "supervise": {"supervise"},
    "datasets": {"supervise"},
    "alerts": {"alerts", "supervise"},
    "bank": {"bank"},
    "integrity": {"integrity"},
    "transcripts": {"transcripts"},
    "archives": {"archives"},
    "verify": {"verify", "documents"},
    "qa": {"qa"},
    "historical-reports": {"historical-reports", "trials"},
}
ADMIN_ONLY_APIS = {"audit"}

# 默认部门（首次启动建立；之后由管理员在「部门与权限」中维护）
DEFAULT_DEPTS = [
    {"code": "SYS", "name": "系统管理部门", "sort": 0, "is_system": True, "data_scope": "all",
     "modules": ASSIGNABLE, "roles": ("admin",),
     "description": "平台管理员所在部门；平台管理员不受部门数据范围限制。"},
    {"code": "XF", "name": "信访室", "sort": 10, "data_scope": "self", "roles": ("xinfang",),
     "modules": ["petitions", *MATERIALS, *SHARED_KNOWLEDGE],
     "description": "信访举报受理、甄别、分办；转线索后可跟踪办理结果。"},
    {"code": "SC", "name": "审查调查室", "sort": 20, "data_scope": "self", "roles": ("banan",),
     "modules": ["clue", "cases", "ctas-materials", "conduct", "inspections", "supervise", "alerts", "bank",
                 "integrity", *MATERIALS, *SHARED_KNOWLEDGE],
     "description": "线索处置、审查调查、监督预警；移送审理后可跟踪审理进度。"},
    {"code": "SL", "name": "案件审理室", "sort": 30, "data_scope": "self", "roles": ("shenli",),
     "modules": ["cases", "trials", "ctas-materials", "integrity", *MATERIALS, *SHARED_KNOWLEDGE],
     "description": "审理受理、阅卷、量纪、审理报告与申诉复查；可查阅移送审理的案件。"},
]
ROLE_DEFAULT_DEPT = {r: d["code"] for d in DEFAULT_DEPTS for r in d["roles"]}

# 历史数据（升级前没有归属）按业务归口落到默认部门；经办人是业务账号的，归经办人部门
HOME_DEPT = {
    "petitions": "XF", "disposition_suggestions": "XF",
    "cases": "SC", "interrogations": "SC", "conduct_cases": "SC", "inspections": "SC",
    "inspection_findings": "SC", "datasets": "SC", "supervise_models": "SC", "alerts": "SC",
    "alert_feedback": "SC", "supervise_dispatches": "SC", "bank_statements": "SC", "person_relations": "SC",
    "person_archives": "SC", "integrity_reply_records": "SC", "archives": "SC", "transcripts": "SC",
    "trials": "SL", "trial_materials": "SL", "appeals": "SL", "documents": "SL", "document_versions": "SL",
    "nexent_conversations": "SL",
}

# 子表随父记录归属（新建时与升级回填共用）
INHERIT = {
    DispositionSuggestion: (Petition, "petition_id"),
    DocumentVersion: (Document, "document_id"),
    Interrogation: (Case, "case_id"),
    TrialMaterial: (Trial, "trial_id"),
    InspectionFinding: (Inspection, "inspection_id"),
    AlertFeedback: (Alert, "alert_id"),
}

OWNED_BASES = (DeptOwned, DeptGrantable, DeptOwnedOrShared)


def _mapped_classes():
    from app.database import Base
    return [m.class_ for m in Base.registry.mappers]


@lru_cache(maxsize=1)
def grantable_classes() -> tuple:
    return tuple(c for c in _mapped_classes() if issubclass(c, DeptGrantable))


def owned_classes():
    return [c for c in _mapped_classes() if issubclass(c, OWNED_BASES)]


# ============================ 数据范围 ============================

@dataclass(frozen=True)
class Scope:
    user_id: int | None
    dept_id: int | None
    dept_ids: tuple[int, ...] | None      # None = 全部部门
    modules: frozenset[str] | None        # None = 全部模块
    label: str = ""

    @property
    def unrestricted(self) -> bool:
        return self.dept_ids is None

    def has_module(self, key: str) -> bool:
        return self.modules is None or key in self.modules


def scope_dept_ids(dept: Department) -> tuple[int, ...] | None:
    if dept.data_scope == "all":
        return None
    ids = [dept.id]
    if dept.data_scope == "custom":
        for x in dept.scope_dept_ids or []:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
    return tuple(dict.fromkeys(ids))


def dept_modules(dept: Department) -> frozenset[str]:
    return frozenset((set(dept.modules or []) & set(ASSIGNABLE)) | ALWAYS)


def build_scope(db: Session, user: User) -> Scope:
    if user.role == "admin":
        return Scope(user.id, user.dept_id, None, None, "平台管理员")
    dept = db.get(Department, user.dept_id) if user.dept_id else None
    if not dept:   # 未分配部门：只有工作台，看不到任何业务数据
        return Scope(user.id, None, (), ALWAYS, "未分配部门")
    if not dept.enabled:
        raise HTTPException(403, "所属部门已停用，请联系管理员")
    return Scope(user.id, dept.id, scope_dept_ids(dept), dept_modules(dept), dept.name)


def apply_scope(db: Session, scope: Scope | None) -> None:
    if scope is None:
        db.info.pop(SCOPE_KEY, None)
    else:
        db.info[SCOPE_KEY] = scope


def current_scope(db: Session) -> Scope | None:
    return db.info.get(SCOPE_KEY)


def check_api_access(path: str, user: User, scope: Scope) -> None:
    """接口级模块把关：路径 /api/<段>/...，段在 API_MODULES 中则须开通其一。"""
    parts = path.split("/")
    if len(parts) < 3 or parts[1] != "api":
        return
    seg = parts[2]
    if seg in ADMIN_ONLY_APIS and user.role != "admin":
        raise HTTPException(403, "仅平台管理员可访问")
    need = API_MODULES.get(seg)
    if need and scope.modules is not None and not (need & scope.modules):
        names = "、".join(MODULE_LABEL.get(k, k) for k in sorted(need))
        raise HTTPException(403, f"本部门未开通相关功能模块（{names}），请联系管理员")


# ============================ ORM 钩子 ============================

def _criteria(ids: tuple[int, ...]):
    ids = list(ids)
    opts = [
        with_loader_criteria(DeptOwned, lambda cls: cls.dept_id.in_(ids), include_aliases=True),
        with_loader_criteria(DeptOwnedOrShared,
                             lambda cls: or_(cls.dept_id.is_(None), cls.dept_id.in_(ids)), include_aliases=True),
    ]
    for cls in grantable_classes():
        granted = select(DataGrant.resource_id).where(
            DataGrant.resource_table == cls.__tablename__, DataGrant.dept_id.in_(ids))
        opts.append(with_loader_criteria(cls, or_(cls.dept_id.in_(ids), cls.id.in_(granted)),
                                         include_aliases=True))
    return opts


def _on_execute(state) -> None:
    scope: Scope | None = state.session.info.get(SCOPE_KEY)
    if scope is None or scope.dept_ids is None or state.execution_options.get("tenancy_bypass"):
        return
    if state.is_select:
        # 已加载对象的属性刷新（如 commit 后过期重载）不再过滤：对象本身已在范围内或由本请求创建
        if state.is_column_load or state.is_relationship_load:
            return
    elif not (state.is_update or state.is_delete):
        return
    state.statement = state.statement.options(*_criteria(scope.dept_ids))


def _on_before_flush(session: Session, _ctx, _instances) -> None:
    scope: Scope | None = session.info.get(SCOPE_KEY)
    for obj in list(session.new):
        if not isinstance(obj, OWNED_BASES) or obj.dept_id is not None:
            continue
        if isinstance(obj, SuperviseModel) and obj.builtin is not False:
            continue   # 内置监督模型为平台共享配置
        did = None
        rule = INHERIT.get(type(obj))
        if rule:
            parent_cls, fk = rule
            pid = getattr(obj, fk, None)
            if pid:
                parent = session.get(parent_cls, pid, execution_options=BYPASS)
                did = parent.dept_id if parent is not None else None
        if did is None and scope is not None:
            did = scope.dept_id
        if did is not None:
            obj.dept_id = did


def install(session_factory) -> None:
    event.listen(session_factory, "do_orm_execute", _on_execute)
    event.listen(session_factory, "before_flush", _on_before_flush)


# ============================ 移交：承办部门 + 授权 ============================

def receivers(db: Session, modules: Iterable[str]) -> list[Department]:
    need = set(modules)
    rows = (db.query(Department).filter(Department.enabled.is_(True), Department.is_system.is_(False))
            .order_by(Department.sort, Department.id).all())
    return [d for d in rows if set(d.modules or []) & need]


def route_to(db: Session, user: User, modules: Iterable[str], dept_id: int | None = None) -> int | None:
    """确定移交的承办部门：显式指定 → 本部门已开通该模块 → 唯一开通该模块的部门；多个可选时须指定。"""
    modules = tuple(modules)
    cands = receivers(db, modules)
    if dept_id:
        if any(d.id == dept_id for d in cands):
            return dept_id
        raise HTTPException(400, "所选承办部门未开通相应功能模块或已停用")
    own = db.get(Department, user.dept_id) if user.dept_id else None
    if own and own.enabled and not own.is_system and set(own.modules or []) & set(modules):
        return own.id
    if len(cands) == 1:
        return cands[0].id
    if not cands:
        return own.id if own else None
    raise HTTPException(400, "有多个部门可承办，请选择承办部门：" + "、".join(d.name for d in cands))


def grant(db: Session, obj, dept_id: int | None, reason: str, user_id: int | None = None) -> None:
    if not dept_id or obj.id is None or obj.dept_id == dept_id:
        return
    table = obj.__tablename__
    hit = (db.query(DataGrant.id).filter(DataGrant.resource_table == table, DataGrant.resource_id == obj.id,
                                         DataGrant.dept_id == dept_id).first())
    if not hit:
        db.add(DataGrant(resource_table=table, resource_id=obj.id, dept_id=dept_id, reason=reason,
                         created_by=user_id))


def link_handoff(db: Session, source, target, reason: str, user: User | None = None) -> None:
    """跨部门移交后双向授权：接收部门可查来源记录，来源部门可跟踪新记录。调用前 target 须已 flush。"""
    if source.dept_id and target.dept_id and source.dept_id != target.dept_id:
        uid = user.id if user else None
        if isinstance(source, DeptGrantable):
            grant(db, source, target.dept_id, reason, uid)
        if isinstance(target, DeptGrantable):
            grant(db, target, source.dept_id, reason, uid)


def dept_name(db: Session, dept_id: int | None) -> str:
    if not dept_id:
        return ""
    d = db.get(Department, dept_id)
    return d.name if d else ""


# ============================ MCP 部门令牌 ============================

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return "ctas_" + secrets.token_urlsafe(32)


def scope_for_mcp_token(db: Session, token: str) -> Scope | None:
    if not token:
        return None
    h = hash_token(token)
    for d in db.query(Department).filter(Department.mcp_token_hash != "", Department.enabled.is_(True)).all():
        if hmac.compare_digest(d.mcp_token_hash or "", h):
            return Scope(None, d.id, scope_dept_ids(d), dept_modules(d), d.name)
    return None


# ============================ 启动：默认部门 + 历史数据归属 ============================

def bootstrap(db: Session) -> dict:
    """幂等：建默认部门、给无部门账号按角色归部门、给无归属的业务数据回填部门、补齐移交授权。"""
    info = {"depts": 0, "users": 0, "rows": 0, "grants": 0}
    if db.query(Department).count() == 0:
        for d in DEFAULT_DEPTS:
            db.add(Department(code=d["code"], name=d["name"], description=d["description"], sort=d["sort"],
                              is_system=d.get("is_system", False), data_scope=d["data_scope"],
                              modules=list(d["modules"]), scope_dept_ids=[]))
            info["depts"] += 1
        db.commit()
    by_code = {d.code: d.id for d in db.query(Department).all()}
    fallback = by_code.get("SYS") or min(by_code.values())

    for u in db.query(User).filter(User.dept_id.is_(None)).all():
        u.dept_id = by_code.get(ROLE_DEFAULT_DEPT.get(u.role, ""), fallback)
        info["users"] += 1
    db.flush()

    users = {u.id: u for u in db.query(User).all()}

    def creator_dept(obj) -> int | None:
        for attr in ("created_by", "uploaded_by", "user_id", "owner_id", "updated_by"):
            u = users.get(getattr(obj, attr, None) or 0)
            if u and u.role != "admin" and u.dept_id:
                return u.dept_id
        return None

    def home(table: str) -> int:
        return by_code.get(HOME_DEPT.get(table, ""), fallback)

    # 顶层业务记录 → 文书（按关联业务记录）→ 子表（随父记录）
    children = set(INHERIT)
    tops = [c for c in owned_classes() if c not in children and c not in (Document,)]
    tops.sort(key=lambda c: c is Alert)   # 预警跟随关联对象，最后回填
    for cls in tops:
        q = db.query(cls).filter(cls.dept_id.is_(None))
        if cls is SuperviseModel:
            q = q.filter(SuperviseModel.builtin.is_(False))
        for obj in q.all():
            did = ref_dept(db, obj.ref_type, obj.ref_id) if cls is Alert else None
            obj.dept_id = did or creator_dept(obj) or home(cls.__tablename__)
            info["rows"] += 1
        db.flush()

    doc_owner: dict[int, int] = {}
    for t in db.query(Trial).all():
        for did in (t.report_doc_id, t.decision_doc_id):
            if did and t.dept_id:
                doc_owner[did] = t.dept_id
    for c in db.query(Case).filter(Case.opened_doc_id.isnot(None)).all():
        if c.dept_id:
            doc_owner[c.opened_doc_id] = c.dept_id
    for a in db.query(Appeal).filter(Appeal.decision_doc_id.isnot(None)).all():
        if a.dept_id:
            doc_owner[a.decision_doc_id] = a.dept_id
    for r in db.query(IntegrityReplyRecord).filter(IntegrityReplyRecord.document_id.isnot(None)).all():
        if r.dept_id:
            doc_owner[r.document_id] = r.dept_id
    for d in db.query(Document).filter(Document.dept_id.is_(None)).all():
        d.dept_id = doc_owner.get(d.id) or creator_dept(d) or home("documents")
        info["rows"] += 1
    db.flush()

    for cls, (parent_cls, fk) in INHERIT.items():
        for obj in db.query(cls).filter(cls.dept_id.is_(None)).all():
            parent = db.get(parent_cls, getattr(obj, fk)) if getattr(obj, fk, None) else None
            obj.dept_id = (parent.dept_id if parent else None) or creator_dept(obj) or home(cls.__tablename__)
            info["rows"] += 1
    db.flush()

    info["grants"] = _backfill_link_grants(db)
    db.commit()
    return info


_ALERT_REF = {"case": Case, "conduct": ConductCase, "inspection": Inspection, "finding": InspectionFinding,
              "petition": Petition, "trial": Trial, "bank": BankStatement}


def ref_dept(db: Session, ref_type: str, ref_id) -> int | None:
    """预警等派生记录的归属：跟随关联业务对象的部门（无对象时由调用方取经办人部门）。"""
    cls = _ALERT_REF.get(ref_type or "")
    if not cls or not str(ref_id or "").isdigit():
        return None
    obj = db.get(cls, int(ref_id), execution_options=BYPASS)
    return obj.dept_id if obj else None


def _backfill_link_grants(db: Session) -> int:
    have = {(g.resource_table, g.resource_id, g.dept_id) for g in db.query(DataGrant).all()}
    added = 0

    def ensure(obj, dept_id, reason):
        nonlocal added
        if not obj or not dept_id or obj.dept_id == dept_id or not isinstance(obj, DeptGrantable):
            return
        key = (obj.__tablename__, obj.id, dept_id)
        if key not in have:
            have.add(key)
            db.add(DataGrant(resource_table=key[0], resource_id=key[1], dept_id=dept_id, reason=reason))
            added += 1

    def pair(a, b, reason):
        if a and b and a.dept_id and b.dept_id and a.dept_id != b.dept_id:
            ensure(a, b.dept_id, reason)
            ensure(b, a.dept_id, reason)

    cases = {c.id: c for c in db.query(Case).all()}
    trials = {t.id: t for t in db.query(Trial).all()}
    pets = {p.id: p for p in db.query(Petition).all()}
    conducts = {c.id: c for c in db.query(ConductCase).all()}
    for c in cases.values():
        pair(c, pets.get(c.linked_petition_id or 0), "信访转线索")
        pair(c, trials.get(c.trial_id or 0), "移送审理")
    for t in trials.values():
        pair(t, cases.get(t.case_id or 0), "移送审理")
    for p in pets.values():
        pair(p, cases.get(p.source_case_id or 0), "信访转线索")
    for c in conducts.values():
        pair(c, cases.get(c.linked_case_id or 0), "党风转线索")
    targets = {"案管": cases, "党风": conducts, "信访": pets}
    for f in db.query(InspectionFinding).filter(InspectionFinding.transfer_ref_id.isnot(None)).all():
        t = targets.get(f.transfer_to or "", {}).get(f.transfer_ref_id)
        if t is not None and f.dept_id and t.dept_id != f.dept_id:
            ensure(t, f.dept_id, "巡察移交")
    db.flush()
    return added


def touch(dept: Department) -> None:
    dept.updated_at = datetime.utcnow()
