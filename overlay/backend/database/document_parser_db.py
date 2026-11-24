# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
import json

from sqlalchemy import text

from database.client import get_db_session


def load_settings(tenant_id, agent_id=0):
    with get_db_session() as db:
        return db.execute(text("""
            SELECT settings FROM nexent.document_parser_settings
            WHERE tenant_id=:tenant AND agent_id=:agent
        """), {"tenant": tenant_id, "agent": agent_id}).scalar_one_or_none() or {}


def save_settings(tenant_id, agent_id, settings, user_id):
    with get_db_session() as db:
        db.execute(text("""
            INSERT INTO nexent.document_parser_settings (tenant_id, agent_id, settings, updated_by)
            VALUES (:tenant, :agent, CAST(:settings AS jsonb), :actor)
            ON CONFLICT (tenant_id, agent_id) DO UPDATE
            SET settings=nexent.document_parser_settings.settings || EXCLUDED.settings,
                updated_by=EXCLUDED.updated_by, updated_at=now()
        """), {"tenant": tenant_id, "agent": agent_id, "settings": json.dumps(settings), "actor": user_id})
