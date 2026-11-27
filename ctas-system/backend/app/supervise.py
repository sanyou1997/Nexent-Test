"""大数据监督模型工厂：内置监督模型（检测器）注册表。

设计：
- 每个"模型"是一个纯函数 detector(db, threshold) -> list[raw_alert]；
  raw_alert = {level, source, title, detail, ref_type, ref_id}。
- 模型是否启用、严重级别、阈值由 supervise_models 表配置（工厂化管理）。
- alerts 路由负责把扫描结果 upsert 进 alerts 表（按 fingerprint 去重）。
- 全部为规则引擎，离线可用，不依赖大模型；仅作"提示"，由人工研判。
"""
from collections import defaultdict
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import (
    Case, ConductCase, Inspection, InspectionFinding, BankStatement,
    Dataset, SuperviseModel, Alert, User,
)


# ---------------- 共享：人员聚合（供线索研判 / 廉政档案复用）----------------
def aggregate_persons(db: Session) -> dict:
    """返回 {姓名: {'cases':[...], 'conduct':[...], 'findings':[...], 'domains':set}}。"""
    persons: dict = defaultdict(lambda: {"cases": [], "conduct": [], "findings": [], "domains": set()})
    for c in db.query(Case).all():
        if c.subject:
            p = persons[c.subject]
            p["cases"].append({"id": c.id, "code": c.code, "title": c.title,
                              "status": c.status, "problem_type": c.problem_type})
            p["domains"].add("案管")
    for cc in db.query(ConductCase).all():
        if cc.subject:
            p = persons[cc.subject]
            p["conduct"].append({"id": cc.id, "title": cc.title, "category": cc.category,
                                "status": cc.status})
            p["domains"].add("党风")
    for f in db.query(InspectionFinding).all():
        # finding 可能含涉及人信息在 content 中，这里仅按涉及单位聚合较粗；
        # 为简单起见，finding 不计入个人（个人画像以 case/conduct 为主）
        pass
    return persons


def person_related_records(db: Session, name: str) -> dict:
    """某人跨模块关联记录（廉政档案/研判画像共用）。"""
    cases = [{"id": c.id, "code": c.code, "title": c.title, "status": c.status,
              "problem_type": c.problem_type, "urgency": c.urgency}
             for c in db.query(Case).filter(Case.subject == name).all()]
    conduct = [{"id": cc.id, "title": cc.title, "category": cc.category, "status": cc.status}
               for cc in db.query(ConductCase).filter(ConductCase.subject == name).all()]
    return {"name": name, "cases": cases, "conduct": conduct,
            "case_count": len(cases), "conduct_count": len(conduct),
            "domains": [d for d in (["案管"] if cases else []) + (["党风"] if conduct else [])]}


# ---------------- 检测器 ----------------
def _det_overdue_case(db: Session, threshold: int) -> list:
    now = datetime.now()
    out = []
    for c in db.query(Case).all():
        if not c.due_at or c.status in ("已处置", "已了结"):
            continue
        days = (c.due_at - now).days
        if days < 0:
            level = "高" if days < -30 else "中"
            out.append({
                "level": level, "source": "案件超期",
                "title": f"线索/案件超期：{c.subject or c.code}（已超期 {abs(days)} 天）",
                "detail": f"案号 {c.code}，状态 {c.status}，审查调查时限 {c.due_at.strftime('%Y-%m-%d')}，已超期 {abs(days)} 天。",
                "ref_type": "case", "ref_id": str(c.id),
            })
    return out


def _det_overdue_rectify(db: Session, threshold: int) -> list:
    now = datetime.now()
    out = []
    for f in db.query(InspectionFinding).all():
        if f.fix_status == "已销号" or not f.due_at:
            continue
        days = (f.due_at - now).days
        if days < 0:
            out.append({
                "level": "高" if days < -30 else "中", "source": "巡察整改超期",
                "title": f"巡察整改超期：{f.content[:24] or '未命名问题'}（已超期 {abs(days)} 天）",
                "detail": f"问题编号 #{f.id}，整改状态 {f.fix_status}，应完成时限 {f.due_at.strftime('%Y-%m-%d')}，已超期 {abs(days)} 天。",
                "ref_type": "finding", "ref_id": str(f.id),
            })
    return out


def _det_repeat_person(db: Session, threshold: int) -> list:
    persons = aggregate_persons(db)
    out = []
    min_hit = max(2, threshold or 2)
    for name, info in persons.items():
        total = len(info["cases"]) + len(info["conduct"])
        if total >= min_hit:
            out.append({
                "level": "中", "source": "重复被反映",
                "title": f"同一人被多次反映：{name}（{total} 次）",
                "detail": f"案管线索 {len(info['cases'])} 条、四风问题 {len(info['conduct'])} 条，建议并案研判。",
                "ref_type": "person", "ref_id": name,
            })
    return out


def _det_related_person(db: Session, threshold: int) -> list:
    persons = aggregate_persons(db)
    out = []
    for name, info in persons.items():
        if len(info["domains"]) >= 2:
            out.append({
                "level": "中", "source": "关联穿透",
                "title": f"跨域关联人员：{name}（涉及 {len(info['domains'])} 个业务域）",
                "detail": f"涉及业务域：{'、'.join(info['domains'])}；建议开展关联分析、穿透核查。",
                "ref_type": "person", "ref_id": name,
            })
    return out


def _det_long_pending(db: Session, threshold: int) -> list:
    now = datetime.now()
    days_limit = threshold or 60
    out = []
    for c in db.query(Case).all():
        if c.status not in ("待研判", "已分办", "初核中"):
            continue
        stayed = (now - c.created_at).days if c.created_at else 0
        if stayed >= days_limit:
            out.append({
                "level": "中", "source": "长期未处置",
                "title": f"线索长期未处置：{c.subject or c.code}（停留 {stayed} 天）",
                "detail": f"案号 {c.code}，当前状态 {c.status}，自登记已 {stayed} 天未进入下一环节（阈值 {days_limit} 天）。",
                "ref_type": "case", "ref_id": str(c.id),
            })
    return out


def _det_inspection_untransferred(db: Session, threshold: int) -> list:
    now = datetime.now()
    days_limit = threshold or 30
    out = []
    for f in db.query(InspectionFinding).all():
        if f.transfer_to and f.transfer_to != "未移交":
            continue
        if f.fix_status not in ("待整改",):
            continue
        stayed = (now - f.created_at).days if f.created_at else 0
        if stayed >= days_limit:
            out.append({
                "level": "低", "source": "巡察未移交",
                "title": f"巡察问题未及时移交/整改：{f.content[:24] or '未命名问题'}",
                "detail": f"问题编号 #{f.id}，发现至今 {stayed} 天未移交办理（阈值 {days_limit} 天），建议推动移交闭环。",
                "ref_type": "finding", "ref_id": str(f.id),
            })
    return out


def _det_bank_high_risk(db: Session, threshold: int) -> list:
    out = []
    for s in db.query(BankStatement).all():
        an = s.analysis or {}
        n = an.get("n_anomalies", 0)
        if n and n > 0:
            out.append({
                "level": "中", "source": "资金异常",
                "title": f"银行流水高风险：{s.filename or '批次' + str(s.id)}（{n} 条异常）",
                "detail": f"批次 #{s.id}，命中异常交易 {n} 条，疑似大额/敏感摘要/高频对手方，建议结合案件核查。",
                "ref_type": "bank", "ref_id": str(s.id),
            })
    return out


# ---------------- 注册表 ----------------
MODELS: dict = {
    "overdue_case": {
        "name": "案件审查调查超期预警", "category": "时限类",
        "description": "案管线索/案件超过审查调查时限且未办结即预警。",
        "default_severity": "中", "default_threshold": 0, "fn": _det_overdue_case,
    },
    "overdue_rectify": {
        "name": "巡察整改超期预警", "category": "时限类",
        "description": "巡察发现问题超过整改时限且未销号即预警。",
        "default_severity": "中", "default_threshold": 0, "fn": _det_overdue_rectify,
    },
    "repeat_person": {
        "name": "同一人重复被反映", "category": "聚集类",
        "description": "同一人在案管/党风中被多次反映（≥阈值次）即提示并案研判。",
        "default_severity": "中", "default_threshold": 2, "fn": _det_repeat_person,
    },
    "related_person": {
        "name": "跨业务域关联穿透", "category": "聚集类",
        "description": "同一人同时涉及案管与党风等多个业务域，提示关联核查。",
        "default_severity": "中", "default_threshold": 2, "fn": _det_related_person,
    },
    "long_pending": {
        "name": "线索长期未处置", "category": "合规类",
        "description": "线索停留某状态超过阈值天数未推进即预警（督办）。",
        "default_severity": "中", "default_threshold": 60, "fn": _det_long_pending,
    },
    "inspection_untransferred": {
        "name": "巡察问题未移交闭环", "category": "合规类",
        "description": "巡察发现问题长期未移交办理/未整改即提示推动闭环。",
        "default_severity": "低", "default_threshold": 30, "fn": _det_inspection_untransferred,
    },
    "bank_high_risk": {
        "name": "银行流水资金异常", "category": "资金类",
        "description": "复用银行流水分析，命中异常交易（大额/敏感摘要/高频对手方）即预警。",
        "default_severity": "中", "default_threshold": 0, "fn": _det_bank_high_risk,
    },
}


def run_model(db: Session, key: str, severity: str, threshold: int) -> list:
    """运行单个检测器，返回带 fingerprint 的 raw alerts。"""
    spec = MODELS.get(key)
    if not spec:
        return []
    raw = spec["fn"](db, threshold)
    for r in raw:
        r["fingerprint"] = f"{key}:{r['ref_type']}:{r['ref_id']}"
        if severity:
            r["level"] = severity
    return raw


def run_all(db: Session, configs: list) -> list:
    """configs: [{key, severity, threshold, enabled}, ...]（仅运行 enabled）。"""
    all_raw = []
    for cfg in configs:
        if not cfg.get("enabled", True):
            continue
        all_raw.extend(run_model(db, cfg["key"], cfg.get("severity", ""), cfg.get("threshold", 0)))
    return all_raw
