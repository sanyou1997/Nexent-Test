"""Runs inside the site's Nexent runtime. Results go into the local checkout only."""
import hashlib
import json
import sys
from contextlib import closing

from sqlalchemy import text
from database.attachment_db import get_file_stream_raw
from database.client import get_db_session
from database.knowledge_db import get_index_name_by_knowledge_name
from management.services.file.access import validate_s3_url_access

request = json.load(sys.stdin)
mode = request["command"]
with get_db_session() as db:
    membership = db.execute(text("""
        SELECT tenant_id,user_id FROM nexent.user_tenant_t
        WHERE lower(user_email)=lower(:email) AND delete_flag='N'
    """), {"email": request["email"]}).one()
    tenant, actor = membership
    if request.get("expected_tenant") and request["expected_tenant"] != tenant:
        raise ValueError("This snapshot is already bound to another tenant.")
    args = {"tenant": tenant, "actor": actor}
    if mode == "seed":
        args.update(models=json.dumps(request["models"]), parser=json.dumps(request["parser"]["tenant"]))
        # Each statement uses the same transaction; existing records are left in place.
        for statement in request["sql"].split(";"):
            if statement.strip():
                db.execute(text(statement), args)
        result = {"models": len(request["models"])}
    elif mode == "agent":
        index = get_index_name_by_knowledge_name(request["kb"], tenant)
        model_id = db.execute(text("""
            SELECT model_id FROM nexent.model_record_t WHERE tenant_id=:tenant
            AND display_name='deepseek-flash' AND delete_flag='N'
        """), args).scalar_one()
        docs = db.execute(text("""
            SELECT original_filename,bucket_name,object_name FROM nexent.knowledge_file_lifecycle_t
            WHERE tenant_id=:tenant AND index_name=:idx AND status='COMPLETED' AND delete_flag='N'
        """), dict(args, idx=index)).all()
        locations = {}
        for name, bucket, key in docs:
            expected = request["hashes"].get("入库文档/" + name)
            if expected is None:
                continue
            if name in locations:
                raise ValueError("Duplicate document: " + name)
            validate_s3_url_access(key, actor, tenant)
            with closing(get_file_stream_raw(key, bucket)) as stream:
                if hashlib.sha256(stream.read()).hexdigest() != expected:
                    raise ValueError("Different edition or changed source: " + name)
            locations[name] = "s3://" + bucket + "/" + key
        expected_names = {s.split("/", 1)[1] for s in request["hashes"] if s.startswith("入库文档/")}
        if set(locations) != expected_names:
            raise ValueError("Upload and finish indexing: " + ", ".join(sorted(expected_names - set(locations))))
        result = dict(tenant=tenant, model_id=model_id, index=index, locations=locations)
    elif mode == "bind":
        agent, version = db.execute(text("""
            SELECT agent_id, current_version_no FROM nexent.ag_tenant_agent_t WHERE
            tenant_id=:tenant AND name='discipline_case_review' AND version_no=0 AND current_version_no>0 AND delete_flag='N'
        """), args).one()
        db.execute(text("""
            INSERT INTO nexent.document_parser_settings(tenant_id,agent_id,settings,updated_by)
            VALUES(:tenant,:agent,CAST(:settings AS jsonb),:actor)
            ON CONFLICT (tenant_id,agent_id) DO NOTHING
        """), dict(args,agent=agent,settings=json.dumps(request["parser"]["agent"])))
        result = dict(tenant=tenant, agent=agent, version=version)
    else:
        raise ValueError(mode)
print(json.dumps(result, ensure_ascii=True))
