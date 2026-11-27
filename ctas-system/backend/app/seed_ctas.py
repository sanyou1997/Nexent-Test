"""CTAS（审理辅助系统）独立库种子：账号 + 法规语料同步 + 审理演示数据。

与全业务 seed.py 的关系：
- 账号：同一套演示账号（审理室 shenli 为主视角）；
- 法规知识库：从全业务 demo.db 全量同步（法规 + 条款 + TF-IDF 向量），
  保证两系统知识库一致——修复后的连续语料两边同时生效（复用不重复造轮子）；
- 业务数据：只育审理域（送审案件/审理事项/送审材料/审理报告），不育信访/党风/巡察等。

幂等：按关键字段判重，可重复运行。
"""
import json
import shutil
import sqlite3
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models import (User, Case, Trial, TrialMaterial, Document, DocumentVersion,
                        PersonArchive, Regulation, RegulationArticle)
from app.security import hash_password
from app.config import settings, BASE_DIR

USERS = [
    ("admin", "Admin@123", "系统管理员", "admin"),
    ("xinfang", "Xf@123456", "信访室", "xinfang"),
    ("banan", "Ba@123456", "办案人员", "banan"),
    ("shenli", "Sl@123456", "审理室", "shenli"),
]


def _seed_users(db):
    if db.query(User).count() == 0:
        for username, pwd, real_name, role in USERS:
            db.add(User(username=username, password_hash=hash_password(pwd),
                        real_name=real_name, role=role))
        db.commit()


def _sync_regulations(db):
    """从全业务 demo.db 同步法规知识库（法规 + 条款 + 向量）到当前库（CTAS）。

    同步口径：以全业务库为源（修复后 20 部 / 1535 条全连续）；CTAS 库为空才同步，
    避免覆盖本地修订。tf_vector 为 JSON 列，直拷。
    """
    if db.query(Regulation).count() > 0:
        return 0
    src_path = BASE_DIR / "data" / "demo.db"
    if not src_path.exists():
        return 0
    src = sqlite3.connect(str(src_path))
    try:
        regs = src.execute(
            "SELECT id, title, version, effective_date, status, source, is_official, "
            "category, required FROM regulations").fetchall()
        n = 0
        for rid, title, version, eff, status, source, official, category, required in regs:
            reg = Regulation(title=title, version=version, effective_date=eff,
                             status=status or "现行有效", source=source,
                             is_official=bool(official), category=category or "其他",
                             required=bool(required))
            db.add(reg)
            db.flush()
            arts = src.execute(
                "SELECT chapter, article_no, content, tf_vector, dense_vector "
                "FROM regulation_articles WHERE regulation_id=? ORDER BY id", (rid,)).fetchall()
            for chapter, article_no, content, tf, dense in arts:
                db.add(RegulationArticle(
                    regulation_id=reg.id, chapter=chapter, article_no=article_no,
                    content=content, tf_vector=json.loads(tf) if tf else None,
                    dense_vector=json.loads(dense) if dense else None))
            n += 1
        db.commit()
        return n
    finally:
        src.close()


def _seed_business(db):
    """审理域演示数据：可选关联案件（c1/c2）+ 2 项审理（t1 关联、t2 独立建档）。

    t2 的 case_id=None，专门演示 CTAS 脱离全业务系统、自带案件信息也能建项跑通全链条。
    """
    if db.query(Trial).count() > 0:
        return
    now = datetime.utcnow()
    # 案件一：已移送审理（材料齐全，审理中）—— 演示「可选关联全业务案件」
    c1 = Case(code="SL20261008001", source="上级交办", subject="李某",
              unit="某区财政局", title="违规收受管理服务对象礼金和财物",
              content="经审查调查，李某在担任区财政局副局长期间，多次收受管理服务对象礼金、财物共计折合人民币18.6万元，并为相关企业在财政补贴审批中提供帮助。审查调查组认定其违反廉洁纪律，构成职务违法。",
              problem_type="违反廉洁纪律", urgency="高", status="已立案",
              disposition="初步核实转立案", disposition_note="2026年8月10日区纪委常委会会议研究决定立案",
              opened_at=now - timedelta(days=52), due_at=now - timedelta(days=8),
              transferred_at=now - timedelta(days=12), created_by=3)
    # 案件二：刚移送待受理
    c2 = Case(code="SL20261015002", source="审计移交", subject="赵某",
              unit="某镇农业服务中心", title="虚报套取惠农补贴资金",
              content="审计发现赵某在惠农补贴发放中虚报面积套取补贴资金9.2万元，审查调查组已查实部分事实。",
              problem_type="职务违法", urgency="中", status="已立案",
              disposition="审计移交立案", disposition_note="2026年9月20日专题会议研究",
              opened_at=now - timedelta(days=30), due_at=now + timedelta(days=25),
              transferred_at=now - timedelta(days=3), created_by=3)
    db.add_all([c1, c2])
    db.commit()

    # 审理事项一：关联案件 + 受理 + 全套材料 + 报告（演示可选关联路径）
    t1 = Trial(case_id=c1.id, title="李某涉嫌违纪违法案审理", status="已报告",
               accept_decision="受理", accept_reason="材料齐全，符合移送条件",
               accepted_at=now - timedelta(days=11),
               interview_at=now - timedelta(days=6),
               interview_attitude="认错悔错",
               interview_note="已与李某见面核对违纪违法事实，其认错悔错，无申辩意见",
               property_disposal="违纪违法所得18.6万元予以收缴；涉嫌犯罪问题随案移送检察机关",
               subject="李某", unit="某区财政局（副局长）", rank="乡科级副职",
               political_status="中共党员", problem_type="违反廉洁纪律",
               source="上级交办", case_status="已立案",
               opened_at=now - timedelta(days=52), involved_amount="18.6万元",
               brief="李某在担任区财政局副局长期间，多次收受管理服务对象礼金、财物共计折合人民币18.6万元，并为相关企业在财政补贴审批中提供帮助。审查调查组认定其违反廉洁纪律，构成职务违法。",
               transfer_from="审查调查组", created_by=4)
    db.add(t1)
    db.commit()

    mats = [
        ("审查调查报告", "审查调查报告", "卷1", 12, "原件", "审查调查组", True),
        ("李某违纪违法事实材料（见面核对）", "违纪事实材料", "卷2", 6, "原件", "审查调查组", True),
        ("证据卷宗（书证/证言/笔录）", "证据卷宗", "卷3-1", 86, "原件", "审查调查组", True),
        ("被审查调查人交代材料及忏悔书", "交代忏悔材料", "卷3-2", 8, "原件", "审查调查组", True),
        ("立案审批表及会议纪要", "程序手续", "卷4-1", 5, "复印件", "案管室", True),
        ("涉案财物清单及处理意见", "涉案财物材料", "卷4-2", 3, "原件", "审查调查组", True),
        ("移送审理审批表", "移送手续", "卷5", 2, "原件", "案管室", True),
    ]
    for name, cat, seq, pages, orig, src_, req in mats:
        db.add(TrialMaterial(trial_id=t1.id, case_id=c1.id, name=name, category=cat,
                             seq=seq, pages=pages, formed_at="2026-09-20",
                             source=src_, is_original=orig, required=req,
                             ocr_status="已识别" if pages > 5 else "",
                             ocr_text=(f"（演示OCR文本）{name}：经识别，本案相关书证、证言相互印证，"
                                       f"李某对收受礼金财物事实供认不讳。" if pages > 5 else "")))
    db.commit()

    # 审理报告文书（AI初稿）
    d = Document(template_id=None, title="SL20261008001——李某涉嫌违纪违法案审理报告（AI初稿）",
                 case_name=c1.code,
                 content="李某涉嫌违纪违法案审理报告\n\n一、被审查调查人基本情况\n李某，某区财政局原副局长。\n\n二、主要违纪违法事实\n……（详见送审材料）\n\n三、证据情况\n证据链完整，多源印证。\n\n四、定性量纪意见\n违反廉洁纪律，构成职务违法，建议给予撤销党内职务、政务撤职处分，违纪违法所得予以收缴。\n\n五、涉案财物处置\n予以收缴，涉嫌犯罪问题随案移送。\n\n六、处理建议\n提请集体审议。",
                 status="AI初稿", created_by=4)
    db.add(d)
    db.flush()
    db.add(DocumentVersion(document_id=d.id, version_no=1, content=d.content,
                           note="审理报告 AI 生成初稿", created_by=4))
    t1.report_doc_id = d.id
    db.commit()

    # 审理事项二：独立建档（case_id=None）—— 演示 CTAS 脱离全业务单独建项跑全链条
    t2 = Trial(case_id=None, title="赵某涉嫌职务违法案审理", status="待阅卷",
               subject="赵某", unit="某镇农业服务中心", rank="管理岗",
               political_status="中共党员", problem_type="职务违法",
               source="审计移交", case_status="已立案",
               opened_at=now - timedelta(days=30), involved_amount="9.2万元",
               brief="审计发现赵某在惠农补贴发放中虚报面积套取补贴资金9.2万元，审查调查组已查实部分事实。",
               transfer_from="审查调查组", created_by=4)
    db.add(t2)
    db.commit()
    db.add(TrialMaterial(trial_id=t2.id, case_id=None, name="初步核实情况报告",
                         category="程序手续", seq="卷1", pages=4, formed_at="2026-09-18",
                         source="审查调查组", is_original="复印件", required=True))
    db.commit()

    # 廉政档案人工维护信息（画像复用演示）
    db.add(PersonArchive(person="李某", unit="某区财政局", post="副局长", rank="乡科级副职",
                         remark="2026年8月立案审查调查"))
    db.commit()


# ---- 历史报告库演示数据（复用共享模块，与全业务 seed 同源） ----


def _seed_historical_reports(db):
    from app import seed_historical
    return seed_historical.run(db, operator_id=4)


def run(db):
    _seed_users(db)
    n = _sync_regulations(db)
    _seed_business(db)
    h = _seed_historical_reports(db)
    return {"regulations_synced": n, "historical_reports": h}
