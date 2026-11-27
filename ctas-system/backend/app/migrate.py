"""轻量迁移与数据保洁（app.main 单应用）。

- migrate(engine)：为旧库补充新增列 / 缺失表（SQLite，幂等）；
- dedup_partial_regulations(engine)：同一法规「节选残本」去重（基名相同判等，
  保留全本删残本）——网页提炼残本曾造成「第1、2条跳到第4条」的丢条事故（2026-10-21）。

注意：两个入口都必须调用，保证 demo.db 与 ctas.db 结构一致、数据同样干净。
"""
import re
import sqlalchemy as sa


def _backfill_document_source(conn) -> None:
    """审理文书库：为旧文书补齐来源（可移植 SQL，SQLite / PostgreSQL 通用）。"""
    conn.execute(sa.text(
        "UPDATE documents SET source = CASE "
        "WHEN content LIKE '%本稿由 Nexent 智能体%' THEN '智能体生成' "
        "WHEN template_id IS NOT NULL AND title NOT LIKE '%审理报告%' THEN '模板生成' "
        "ELSE '流程生成' END WHERE source IS NULL OR source = ''"))


def add_missing_columns(engine) -> list:
    """按 ORM 模型为已有表补齐缺失列（任意数据库通用；新列一律可空、不带默认值，避免锁表重写）。"""
    from app.database import Base
    added = []
    insp = sa.inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                conn.execute(sa.text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))
                added.append(f"{table.name}.{col.name}")
    return added


def migrate(engine) -> None:
    """轻量迁移。

    PostgreSQL：表结构由 ORM create_all 一次建全，这里只做可移植的数据回填；
    SQLite：保留历史补列逻辑（为旧版 demo.db / ctas.db 逐列 ALTER，幂等）。
    """
    if engine.dialect.name != "sqlite":
        add_missing_columns(engine)
        with engine.begin() as conn:
            _backfill_document_source(conn)
        return
    with engine.begin() as conn:
        # 审理文书库：文书来源 / 版本来源
        dcols = [c["name"] for c in sa.inspect(conn).get_columns("documents")]
        if "source" not in dcols:
            conn.execute(sa.text("ALTER TABLE documents ADD COLUMN source VARCHAR(20) DEFAULT ''"))
        vcols = [c["name"] for c in sa.inspect(conn).get_columns("document_versions")]
        if "source" not in vcols:
            conn.execute(sa.text("ALTER TABLE document_versions ADD COLUMN source VARCHAR(20) DEFAULT ''"))
        _backfill_document_source(conn)
        cols = [c["name"] for c in sa.inspect(conn).get_columns("regulations")]
        if "is_official" not in cols:
            conn.execute(sa.text("ALTER TABLE regulations ADD COLUMN is_official BOOLEAN DEFAULT 0"))
        if "category" not in cols:
            conn.execute(sa.text("ALTER TABLE regulations ADD COLUMN category VARCHAR(50) DEFAULT '其他'"))
        if "required" not in cols:
            conn.execute(sa.text("ALTER TABLE regulations ADD COLUMN required BOOLEAN DEFAULT 0"))
        # 文书模板库深化：来源标注 / 字段校验规则 / 常用置顶
        tcols2 = [c["name"] for c in sa.inspect(conn).get_columns("document_templates")]
        for col, typ in [("source", "VARCHAR(20)"), ("field_rules", "JSON"), ("pinned", "BOOLEAN DEFAULT 0")]:
            if col not in tcols2:
                conn.execute(sa.text(f"ALTER TABLE document_templates ADD COLUMN {col} {typ}"))
        # 廉政档案：关系双向标记
        prcols = [c["name"] for c in sa.inspect(conn).get_columns("person_relations")]
        if "bidirectional" not in prcols:
            conn.execute(sa.text("ALTER TABLE person_relations ADD COLUMN bidirectional BOOLEAN DEFAULT 0"))
        tcols = [c["name"] for c in sa.inspect(conn).get_columns("transcripts")]
        if "audio_path" not in tcols:
            conn.execute(sa.text("ALTER TABLE transcripts ADD COLUMN audio_path VARCHAR(300) DEFAULT ''"))
        # ASR 深化：说话人分离 + 会议纪要
        for col, typ in [
            ("lang", "VARCHAR(10)"), ("speaker_count", "INTEGER"),
            ("speaker_names", "JSON"), ("minutes", "TEXT"), ("minutes_json", "JSON"),
            ("minutes_status", "VARCHAR(20)"), ("minutes_model", "VARCHAR(40)"),
            ("minutes_error", "VARCHAR(300)"),
        ]:
            if col not in tcols:
                conn.execute(sa.text(f"ALTER TABLE transcripts ADD COLUMN {col} {typ}"))
        pcols = [c["name"] for c in sa.inspect(conn).get_columns("petitions")]
        for col, typ in [("summary", "TEXT"), ("summary_mode", "VARCHAR(10)"), ("biz_type", "VARCHAR(20)")]:
            if col not in pcols:
                conn.execute(sa.text(f"ALTER TABLE petitions ADD COLUMN {col} {typ}"))
        if "tf_vector" not in pcols:
            conn.execute(sa.text("ALTER TABLE petitions ADD COLUMN tf_vector JSON"))
        # 受理法治化 / 办理法治化 / 监督追责法治化
        for col, typ in [
            ("petitioner_type", "VARCHAR(10)"), ("contact", "VARCHAR(120)"),
            ("accept_decision", "VARCHAR(20)"), ("accept_reason", "TEXT"),
            ("notified_at", "TIMESTAMP"), ("due_at", "TIMESTAMP"),
            ("feedback_at", "TIMESTAMP"), ("feedback_note", "TEXT"),
            ("transfer_to", "VARCHAR(120)"), ("source_case_id", "INTEGER"),
            ("supervised_at", "TIMESTAMP"), ("supervise_note", "TEXT"),
        ]:
            if col not in pcols:
                conn.execute(sa.text(f"ALTER TABLE petitions ADD COLUMN {col} {typ}"))
        # 材料原件（登记时上传的 PDF / 图片）
        for col, typ in [("file_path", "VARCHAR(300)"), ("file_name", "VARCHAR(200)"), ("file_type", "VARCHAR(20)")]:
            if col not in pcols:
                conn.execute(sa.text(f"ALTER TABLE petitions ADD COLUMN {col} {typ}"))
        fcols = [c["name"] for c in sa.inspect(conn).get_columns("inspection_findings")]
        if "due_at" not in fcols:
            conn.execute(sa.text("ALTER TABLE inspection_findings ADD COLUMN due_at TIMESTAMP"))
        for col, typ in [("fix_req", "TEXT"), ("fix_note", "TEXT"), ("fix_at", "TIMESTAMP"),
                         ("fix_effect", "VARCHAR(20)")]:
            if col not in fcols:
                conn.execute(sa.text(f"ALTER TABLE inspection_findings ADD COLUMN {col} {typ}"))
        acols = [c["name"] for c in sa.inspect(conn).get_columns("archives")]
        for col, typ in [("file_path", "VARCHAR(300)"), ("secret_level", "VARCHAR(20)"),
                         ("retention", "VARCHAR(20)"), ("content_hash", "VARCHAR(64)"),
                         ("boxes_json", "TEXT"), ("ocr_dpi", "INTEGER")]:
            if col not in acols:
                conn.execute(sa.text(f"ALTER TABLE archives ADD COLUMN {col} {typ}"))
        tcols = [c["name"] for c in sa.inspect(conn).get_columns("trials")]
        for col, typ in [
            ("accept_decision", "VARCHAR(20)"), ("accept_reason", "TEXT"), ("accepted_at", "TIMESTAMP"),
            ("interview_at", "TIMESTAMP"), ("interview_attitude", "VARCHAR(20)"), ("interview_note", "TEXT"),
            ("returned_at", "TIMESTAMP"), ("return_reason", "TEXT"), ("return_to", "VARCHAR(120)"),
            ("executed_at", "TIMESTAMP"), ("execute_note", "TEXT"), ("property_disposal", "TEXT"),
            ("physical_evidence", "TEXT"), ("evidence_review", "TEXT"),
            # P2 见面谈话专业化（规则第五十一~五十三条）
            ("interviewers", "VARCHAR(120)"), ("recorder", "VARCHAR(50)"),
            ("interview_place", "VARCHAR(120)"), ("rights_notified", "INTEGER DEFAULT 0"),
            ("defense_items", "JSON"), ("interview_summary", "TEXT"),
            # P3 处分决定书 + 执行回证
            ("decision_doc_id", "INTEGER"), ("decision_no", "VARCHAR(60)"),
            ("decision_result", "TEXT"), ("decision_date", "TIMESTAMP"),
            ("decision_approver", "VARCHAR(120)"),
            ("execute_announcer", "VARCHAR(120)"), ("execute_scope", "VARCHAR(200)"),
            ("execute_delivery", "VARCHAR(60)"), ("execute_signed", "INTEGER DEFAULT 0"),
            ("execution_items", "JSON"), ("restore_rights_at", "TIMESTAMP"),
            ("execution_closed", "INTEGER DEFAULT 0"), ("execution_closed_at", "TIMESTAMP"),
        ]:
            if col not in tcols:
                conn.execute(sa.text(f"ALTER TABLE trials ADD COLUMN {col} {typ}"))
        # CTAS 独立建档：审理事项自带案件核心字段（脱离全业务案件也能建项）
        tcols2 = [c["name"] for c in sa.inspect(conn).get_columns("trials")]
        for col, typ in [
            ("subject", "VARCHAR(80)"), ("unit", "VARCHAR(120)"), ("rank", "VARCHAR(40)"),
            ("political_status", "VARCHAR(20)"), ("problem_type", "VARCHAR(40)"),
            ("source", "VARCHAR(20)"), ("case_status", "VARCHAR(20)"),
            ("opened_at", "TIMESTAMP"), ("involved_amount", "VARCHAR(40)"),
            ("brief", "TEXT"), ("transfer_from", "VARCHAR(120)"),
        ]:
            if col not in tcols2:
                conn.execute(sa.text(f"ALTER TABLE trials ADD COLUMN {col} {typ}"))
        # 银行流水：完整性质检门禁
        bcols = [c["name"] for c in sa.inspect(conn).get_columns("bank_statements")]
        for col, typ in [
            ("source_rows", "INTEGER"), ("parsed_rows", "INTEGER"), ("skipped_rows", "INTEGER"),
            ("missing_report", "TEXT"), ("completeness_score", "FLOAT"), ("completeness_level", "VARCHAR(10)"),
            ("header_row", "INTEGER"), ("mapping", "TEXT"), ("template", "VARCHAR(60)"),
        ]:
            if col not in bcols:
                conn.execute(sa.text(f"ALTER TABLE bank_statements ADD COLUMN {col} {typ}"))
        # 智能监督：用户自建碰撞模型 + 数据集接入
        mcols = [c["name"] for c in sa.inspect(conn).get_columns("supervise_models")]
        for col, typ in [("kind", "VARCHAR(10)"), ("config", "TEXT"), ("owner_id", "INTEGER")]:
            if col not in mcols:
                conn.execute(sa.text(f"ALTER TABLE supervise_models ADD COLUMN {col} {typ}"))
        if "datasets" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE datasets (id INTEGER PRIMARY KEY, name VARCHAR(120), source_filename VARCHAR(200), "
                "columns TEXT, row_count INTEGER, rows_json TEXT, note VARCHAR(200), "
                "created_by INTEGER, created_at TIMESTAMP)"))
        if "person_relations" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE person_relations (id INTEGER PRIMARY KEY, person VARCHAR(80), "
                "related_name VARCHAR(80), relation_type VARCHAR(40), source VARCHAR(40), note TEXT, "
                "created_by INTEGER, created_at TIMESTAMP)"))
        if "appeals" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE appeals (id INTEGER PRIMARY KEY, code VARCHAR(30), trial_id INTEGER, "
                "case_id INTEGER, appellant VARCHAR(80), appellant_unit VARCHAR(120), reason TEXT, "
                "source VARCHAR(40), appeal_at TIMESTAMP, status VARCHAR(20), accept_decision VARCHAR(20), "
                "accept_reason TEXT, accepted_at TIMESTAMP, reviewer_id INTEGER, recusal_note TEXT, "
                "review_note TEXT, conclusion VARCHAR(20), conclusion_reason TEXT, decision_doc_id INTEGER, "
                "reviewed_at TIMESTAMP, served_at TIMESTAMP, served_note TEXT, reappealed BOOLEAN, "
                "reappeal_note TEXT, finalized_at TIMESTAMP, correction_note TEXT, "
                "created_by INTEGER, created_at TIMESTAMP)"))
        ccols = [c["name"] for c in sa.inspect(conn).get_columns("conduct_cases")]
        for col, typ in [
            ("rectify_req", "TEXT"), ("rectify_due", "VARCHAR(20)"),
            ("rectify_note", "TEXT"), ("rectified_at", "TIMESTAMP"),
            ("rectify_issued_at", "TIMESTAMP"), ("rectify_due_at", "TIMESTAMP"),
            ("rectify_stage", "VARCHAR(20)"), ("rectify_report_at", "TIMESTAMP"),
            ("rectify_closed_at", "TIMESTAMP"), ("rectify_closed_by", "VARCHAR(60)"),
        ]:
            if col not in ccols:
                conn.execute(sa.text(f"ALTER TABLE conduct_cases ADD COLUMN {col} {typ}"))
        # 案管全流程合规：初核起点 / 留置合规节点 / 移送审理关联
        case_cols = [c["name"] for c in sa.inspect(conn).get_columns("cases")]
        for col, typ in [
            ("chuhe_at", "TIMESTAMP"), ("liuzhi_status", "VARCHAR(20)"),
            ("liuzhi_approved_by", "VARCHAR(120)"), ("liuzhi_start", "TIMESTAMP"),
            ("liuzhi_end", "TIMESTAMP"), ("liuzhi_extended", "BOOLEAN"),
            ("liuzhi_release_at", "TIMESTAMP"), ("liuzhi_notify_at", "TIMESTAMP"),
            ("liuzhi_notify_note", "TEXT"), ("trial_id", "INTEGER"),
            ("transferred_at", "TIMESTAMP"),
        ]:
            if col not in case_cols:
                conn.execute(sa.text(f"ALTER TABLE cases ADD COLUMN {col} {typ}"))
        # CTAS 送审材料表（旧库补建）
        if "trial_materials" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE trial_materials (id INTEGER PRIMARY KEY, trial_id INTEGER, "
                "case_id INTEGER, name VARCHAR(200), category VARCHAR(40), seq VARCHAR(30), "
                "pages INTEGER DEFAULT 0, formed_at VARCHAR(20), source VARCHAR(60), "
                "is_original VARCHAR(10), sign_note VARCHAR(120), required BOOLEAN DEFAULT 0, "
                "file_path VARCHAR(300), file_name VARCHAR(200), file_type VARCHAR(20), "
                "ocr_text TEXT, ocr_status VARCHAR(10), ocr_at TIMESTAMP, note TEXT, "
                "created_by INTEGER, created_at TIMESTAMP)"))
        if "interrogations" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE interrogations (id INTEGER PRIMARY KEY, case_id INTEGER, "
                "interrogated VARCHAR(80), interrogator VARCHAR(80), at TIMESTAMP, "
                "location VARCHAR(120), duration_hours FLOAT, rights_informed BOOLEAN, "
                "rest_meal_ok BOOLEAN, note TEXT, created_by INTEGER, created_at TIMESTAMP)"))

        # CTAS 历史报告库（批量导入 + 类案参考 + 多维统计）
        if "historical_reports" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE historical_reports (id INTEGER PRIMARY KEY, "
                "report_no VARCHAR(60), title VARCHAR(300), report_type VARCHAR(40), "
                "report_date VARCHAR(20), year INTEGER, region VARCHAR(60), "
                "region_code VARCHAR(20), audited_org VARCHAR(150), audit_type VARCHAR(40), "
                "problem_category VARCHAR(40), severity VARCHAR(20), amount FLOAT, "
                "subject VARCHAR(200), findings TEXT, conclusion TEXT, legal_refs TEXT, "
                "tags TEXT, content TEXT, char_count INTEGER, page_count INTEGER, "
                "file_name VARCHAR(200), file_type VARCHAR(20), file_path VARCHAR(300), "
                "content_hash VARCHAR(64), extract_confidence FLOAT, missing_fields TEXT, "
                "status VARCHAR(20), source VARCHAR(60), tf_vector TEXT, "
                "uploaded_by INTEGER, created_at TIMESTAMP)"))
        if "import_batches" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE import_batches (id INTEGER PRIMARY KEY, "
                "batch_no VARCHAR(40), total INTEGER, ok INTEGER, duplicated INTEGER, "
                "failed INTEGER, need_review INTEGER, detail_json TEXT, note VARCHAR(200), "
                "created_by INTEGER, created_at TIMESTAMP)"))
        if "report_findings" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE report_findings (id INTEGER PRIMARY KEY, "
                "report_id INTEGER, seq VARCHAR(20), title VARCHAR(200), content TEXT, "
                "amount FLOAT, problem_type VARCHAR(40), severity VARCHAR(20), "
                "legal_refs TEXT, created_at TIMESTAMP)"))
        # 类案引用留痕（谁、何时、引用了哪份报告/哪条问题）
        if "report_references" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE report_references (id INTEGER PRIMARY KEY, "
                "report_id INTEGER, finding_id INTEGER, query VARCHAR(200), "
                "note VARCHAR(200), user_id INTEGER, created_at TIMESTAMP)"))
        # 历史报告：疑似重复标记（文号相同但内容不同）
        hcols = [c["name"] for c in sa.inspect(conn).get_columns("historical_reports")]
        if "near_duplicate_of" not in hcols:
            conn.execute(sa.text(
                "ALTER TABLE historical_reports ADD COLUMN near_duplicate_of INTEGER DEFAULT 0"))

        # 督办中枢：一键督办下发记录表
        if "supervise_dispatches" not in sa.inspect(conn).get_table_names():
            conn.execute(sa.text(
                "CREATE TABLE supervise_dispatches ("
                "id INTEGER PRIMARY KEY, source VARCHAR(20), ref_type VARCHAR(20), "
                "ref_id INTEGER, ref_code VARCHAR(40), ref_title VARCHAR(200), "
                "ref_unit VARCHAR(100), level VARCHAR(10), urge_to VARCHAR(100), "
                "note TEXT, status VARCHAR(20), created_by INTEGER, created_at TIMESTAMP, "
                "feedback TEXT, feedback_at TIMESTAMP, "
                "due_at TIMESTAMP, escalate_count INTEGER DEFAULT 0, escalated_at TIMESTAMP)"))

        # 监督闭环深耕：预警处置时限 SLA + 督办办理期限/逾期自动升级
        alcols = [c["name"] for c in sa.inspect(conn).get_columns("alerts")]
        if "due_at" not in alcols:
            conn.execute(sa.text("ALTER TABLE alerts ADD COLUMN due_at TIMESTAMP"))
            conn.execute(sa.text("UPDATE alerts SET due_at = datetime(created_at, '+24 hours') WHERE level = '高'"))
            conn.execute(sa.text("UPDATE alerts SET due_at = datetime(created_at, '+72 hours') WHERE level = '中'"))
            conn.execute(sa.text("UPDATE alerts SET due_at = datetime(created_at, '+168 hours') WHERE level = '低'"))
        dpcols = [c["name"] for c in sa.inspect(conn).get_columns("supervise_dispatches")]
        for col, typ in [("due_at", "TIMESTAMP"), ("escalate_count", "INTEGER DEFAULT 0"),
                         ("escalated_at", "TIMESTAMP")]:
            if col not in dpcols:
                conn.execute(sa.text(f"ALTER TABLE supervise_dispatches ADD COLUMN {col} {typ}"))
        conn.execute(sa.text(
            "UPDATE supervise_dispatches SET due_at = datetime(created_at, '+3 days') "
            "WHERE due_at IS NULL AND level = '超期' AND status != '已闭环'"))
        conn.execute(sa.text(
            "UPDATE supervise_dispatches SET due_at = datetime(created_at, '+7 days') "
            "WHERE due_at IS NULL AND level = '临期' AND status != '已闭环'"))

    add_missing_columns(engine)  # 新增模型列统一走通用补列，不再逐列手写 ALTER


# 部门隔离后改为「部门内唯一」的自然键（原为全局唯一）
_DEPT_UNIQUE = [
    ("person_archives", "person", "uq_person_archives_dept_person"),
    ("alerts", "fingerprint", "uq_alerts_dept_fingerprint"),
    ("alert_feedback", "fingerprint", "uq_alert_feedback_dept_fingerprint"),
]


def migrate_tenancy(engine) -> list:
    """部门（租户）隔离的结构迁移（幂等，SQLite / PostgreSQL 通用）：
    补 dept_id 列与索引；人员档案、预警指纹由全局唯一改为部门内唯一（各部门各自建档 / 各自预警）。"""
    from app.database import Base
    done = add_missing_columns(engine)
    insp = sa.inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            if table.name in tables and "dept_id" in table.columns:
                conn.execute(sa.text(
                    f'CREATE INDEX IF NOT EXISTS "ix_{table.name}_dept_id" ON "{table.name}" (dept_id)'))
        for table, col, uq in _DEPT_UNIQUE:
            if table not in tables:
                continue
            old = f"ix_{table}_{col}"
            if any(i["name"] == old and i.get("unique") for i in insp.get_indexes(table)):
                conn.execute(sa.text(f'DROP INDEX "{old}"'))
                conn.execute(sa.text(f'CREATE INDEX "{old}" ON "{table}" ({col})'))
                done.append(f"{table}.{col}: 全局唯一 → 部门内唯一")
            conn.execute(sa.text(f'CREATE UNIQUE INDEX IF NOT EXISTS "{uq}" ON "{table}" (dept_id, {col})'))
    return done


_ORDINAL = re.compile(r"（[一二三四五六七八九十〇零\d]+）")


def dedup_partial_regulations(engine) -> list:
    """同一法规「节选残本」去重：基名相同（去掉括注）时，删条数更少的残本、保全本。

    不能用标题互相包含判等——「监察法」是「监察法实施条例」标题的子串，
    互相包含规则会误删全本（2026-10-21 实测踩坑，已修正为基名判等）。
    """
    def base_title(t: str) -> str:
        # 去掉版本 / 节选等括注；序号括注（「（二）」「（3）」）表示另一部文件，必须保留——
        # 否则《贪污贿赂解释》与《贪污贿赂解释（二）》判为同一法规，每次启动删掉前者（2026-11-26 发现）
        return re.sub(r"（[^（）]*）", lambda m: m.group(0) if _ORDINAL.fullmatch(m.group(0)) else "",
                      t or "").strip()

    with engine.begin() as conn:
        rows = conn.execute(sa.text(
            "SELECT r.id, r.title, "
            "(SELECT COUNT(*) FROM regulation_articles a WHERE a.regulation_id = r.id) AS n "
            "FROM regulations r")).fetchall()
    regs = [{"id": r[0], "title": r[1], "n": r[2]} for r in rows]
    removed = []
    for a in regs:
        for b in regs:
            if a["id"] == b["id"] or not a["title"] or not b["title"]:
                continue
            if (base_title(a["title"]) == base_title(b["title"])
                    and a["title"] != b["title"] and a["n"] < b["n"]):
                with engine.begin() as conn:
                    conn.execute(sa.text("DELETE FROM regulation_articles WHERE regulation_id = :id"),
                                 {"id": a["id"]})
                    conn.execute(sa.text("DELETE FROM regulations WHERE id = :id"), {"id": a["id"]})
                removed.append(f"{a['title']}（{a['n']}条，全本 {b['title']} {b['n']}条）")
                break
    return removed
