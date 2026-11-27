"""模型预警误报反馈：共享助手（alerts 路由 / supervise 路由复用，避免循环 import）。

fingerprint 规约（全系统统一）：
- 内置模型：<model_key>:<ref_type>:<ref_id>     （见 app/supervise.py run_detector）
- 用户模型：user:<model_key>:<对象key>          （见 routers/supervise.py run_user_model）

因此从 fingerprint 可反解出 model_key 与 obj_key，支撑「误报抑制」：
某模型下某对象已被人工判为误报 → 再次扫描/运行时跳过，不再重复打扰。
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Alert, AlertFeedback, User

VERDICT_FALSE = "误报"
VERDICT_TRUE = "属实"
VERDICTS = (VERDICT_FALSE, VERDICT_TRUE)


def model_key_of(fp: str) -> str:
    """fingerprint → 模型 key（用户模型剥掉 'user:' 前缀）。"""
    parts = (fp or "").split(":")
    if not parts or not parts[0]:
        return ""
    if parts[0] == "user" and len(parts) >= 2:
        return parts[1]
    return parts[0]


def obj_key_of(fp: str) -> str:
    """fingerprint → 命中对象 key（抑制比对用，与模型重新产出时的 key 同形）。"""
    parts = (fp or "").split(":")
    if parts and parts[0] == "user":
        return ":".join(parts[2:]) if len(parts) > 2 else ""
    return ":".join(parts[1:]) if len(parts) > 1 else ""


def false_keys(db: Session) -> dict:
    """{model_key: set(obj_key)}：已被判误报、应在再次扫描/运行时抑制的对象集合。"""
    out: dict = {}
    rows = db.query(AlertFeedback).filter(AlertFeedback.verdict == VERDICT_FALSE).all()
    for r in rows:
        out.setdefault(r.model_key or "", set()).add(r.obj_key or "")
    return out


def is_suppressed(db: Session, model_key: str, obj_key: str) -> bool:
    return obj_key in false_keys(db).get(model_key or "", set())


def verdict_map(db: Session, fingerprints: list) -> dict:
    """{fingerprint: {"verdict","reason","by","at"}}：列表页一次性取反馈，避免 N+1。"""
    fps = [f for f in fingerprints if f]
    if not fps:
        return {}
    uids = set()
    rows = db.query(AlertFeedback).filter(AlertFeedback.fingerprint.in_(fps)).all()
    for r in rows:
        if r.created_by:
            uids.add(r.created_by)
    users = {u.id: (u.real_name or u.username) for u in
             db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    return {
        r.fingerprint: {
            "verdict": r.verdict, "reason": r.reason,
            "by": users.get(r.created_by, ""),
            "at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        }
        for r in rows
    }


def record(db: Session, alert: Alert, verdict: str, reason: str, user: User) -> dict:
    """记录/覆盖反馈（一预警一条）；同步推进预警状态：误报→已忽略，属实→已确认。"""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict 非法：{verdict}")
    reason = (reason or "").strip()[:300]
    if verdict == VERDICT_FALSE and not reason:
        raise ValueError("标记误报必须填写理由（作为模型调优依据）")
    fb = (db.query(AlertFeedback)   # 反馈与预警同部门（指纹为部门内唯一）
          .filter(AlertFeedback.fingerprint == alert.fingerprint, AlertFeedback.dept_id == alert.dept_id).first())
    if fb:
        fb.verdict = verdict
        fb.reason = reason
        fb.created_by = user.id
        fb.created_at = datetime.utcnow()
    else:
        fb = AlertFeedback(
            alert_id=alert.id, fingerprint=alert.fingerprint,
            model_key=model_key_of(alert.fingerprint),
            obj_key=obj_key_of(alert.fingerprint),
            verdict=verdict, reason=reason, created_by=user.id, dept_id=alert.dept_id)
        db.add(fb)
    # 状态联动：误报等同人工忽略；属实等同确认（留痕 acked_by/at）
    alert.status = "已忽略" if verdict == VERDICT_FALSE else "已确认"
    alert.acked_by = user.id
    alert.acked_at = datetime.now()
    db.commit()
    return {"verdict": verdict, "reason": reason}


def revoke(db: Session, alert: Alert) -> bool:
    """撤销反馈：删除记录并把预警退回「待处理」，恢复模型再次产出资格。"""
    fb = (db.query(AlertFeedback)   # 反馈与预警同部门（指纹为部门内唯一）
          .filter(AlertFeedback.fingerprint == alert.fingerprint, AlertFeedback.dept_id == alert.dept_id).first())
    if not fb:
        return False
    db.delete(fb)
    if alert.status in ("已忽略", "已确认") and alert.acked_by == fb.created_by:
        alert.status = "待处理"
    db.commit()
    return True


def model_stats(db: Session, models: list) -> dict:
    """误报反馈统计：总体 + 分模型（命中预警数/已反馈/误报/属实/误报率）。

    models: [{key, name, kind}]（来自模型目录）。误报率 = 误报 / 已反馈，
    已反馈为 0 时返回 None（前端显示「—」，不制造 0% 的虚假精度）。
    """
    alerts = db.query(Alert).all()
    total_by_model: dict = {}
    for a in alerts:
        mk = model_key_of(a.fingerprint)
        total_by_model[mk] = total_by_model.get(mk, 0) + 1
    fbs = db.query(AlertFeedback).all()
    fb_by_model: dict = {}
    for f in fbs:
        d = fb_by_model.setdefault(f.model_key or "", {"feedback": 0, "false": 0, "true": 0})
        d["feedback"] += 1
        if f.verdict == VERDICT_FALSE:
            d["false"] += 1
        else:
            d["true"] += 1
    name_of = {m["key"]: m.get("name") or m["key"] for m in models}
    per_model = []
    for key in sorted(set(total_by_model) | set(fb_by_model)):
        d = fb_by_model.get(key, {"feedback": 0, "false": 0, "true": 0})
        rate = round(d["false"] / d["feedback"], 3) if d["feedback"] else None
        per_model.append({
            "model_key": key, "name": name_of.get(key, key),
            "alerts": total_by_model.get(key, 0),
            "feedback": d["feedback"], "false": d["false"], "true": d["true"],
            "false_rate": rate,
        })
    n_fb = sum(d["feedback"] for d in fb_by_model.values())
    n_false = sum(d["false"] for d in fb_by_model.values())
    n_true = sum(d["true"] for d in fb_by_model.values())
    return {
        "total_alerts": len(alerts),
        "feedback": n_fb, "false": n_false, "true": n_true,
        "false_rate": round(n_false / n_fb, 3) if n_fb else None,
        "per_model": per_model,
    }


def feedback_rows(db: Session, limit: int = 300) -> list:
    """反馈记录清单（供「误报分析」页展示与撤销）。"""
    rows = (db.query(AlertFeedback)
            .order_by(AlertFeedback.created_at.desc()).limit(limit).all())
    alerts = {a.fingerprint: a for a in
              db.query(Alert).filter(Alert.fingerprint.in_([r.fingerprint for r in rows])).all()} \
        if rows else {}
    uids = {r.created_by for r in rows if r.created_by}
    users = {u.id: (u.real_name or u.username) for u in
             db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    out = []
    for r in rows:
        a = alerts.get(r.fingerprint)
        out.append({
            "id": r.id, "alert_id": r.alert_id, "fingerprint": r.fingerprint,
            "model_key": r.model_key, "obj_key": r.obj_key,
            "verdict": r.verdict, "reason": r.reason,
            "title": (a.title if a else ""),
            "by": users.get(r.created_by, ""),
            "at": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else "",
        })
    return out
