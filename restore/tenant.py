"""Runs inside the site's Nexent runtime. Results go into the local checkout only."""
import hashlib
import json
import sys
from contextlib import closing
from urllib.parse import urlsplit

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
        result = dict(tenant=tenant, index=index, locations=locations)
    elif mode == "review-settings":
        # Model IDs are assigned by the destination and differ across installations.
        draft = db.execute(text("""
            SELECT agent_id, model_ids, model_params_override, requested_output_tokens, current_version_no
            FROM nexent.ag_tenant_agent_t WHERE tenant_id=:tenant AND created_by=:actor
            AND name='discipline_case_review' AND version_no=0 AND delete_flag='N' FOR UPDATE
        """), args).mappings().one()
        ids = draft["model_ids"] or []
        if len(ids) != 1:
            raise ValueError("Select one conversation model for the review agent first.")
        model_id = str(ids[0])
        from database.model_management_db import get_model_records
        model, = get_model_records({"model_id": ids[0]}, tenant)
        if (urlsplit(model["base_url"]).hostname != "api.deepseek.com" or
                model["model_name"].lower() not in {"deepseek-flash", "deepseek-v4-flash"}):
            raise ValueError("This preset targets the official DeepSeek Flash API. Set limits for this model in the agent editor.")
        limits = dict(max_input_tokens=65536, max_output_tokens=32768, context_window_tokens=98304)
        if any(model.get(k) and model[k] < v for k, v in limits.items()):
            raise ValueError("A configured model capacity is below this preset. Check the model limits in Nexent.")
        overrides = dict(draft["model_params_override"] or {})
        params = dict(overrides.get(model_id) or {})
        extra = dict(params.get("extra_params") or {})
        extra["enable_thinking"] = True
        params.update(limits, extra_params=extra)
        overrides[model_id] = params
        args.update(agent=draft["agent_id"], value=json.dumps(overrides), output=limits["max_output_tokens"])
        if overrides != draft["model_params_override"] or draft["requested_output_tokens"] != args["output"]:
            db.execute(text("""
                UPDATE nexent.ag_tenant_agent_t SET model_params_override=CAST(:value AS jsonb),
                requested_output_tokens=:output, updated_by=:actor WHERE tenant_id=:tenant
                AND agent_id=:agent AND version_no=0
            """), args)
        published = db.execute(text("""
            SELECT model_ids, model_params_override, requested_output_tokens FROM nexent.ag_tenant_agent_t
            WHERE tenant_id=:tenant AND agent_id=:agent AND version_no=:version AND version_no>0
        """), dict(args, version=draft["current_version_no"])).mappings().first()
        result = {"published": bool(published and published["model_ids"] == ids
                                   and (published["model_params_override"] or {}).get(model_id) == params
                                   and published["requested_output_tokens"] == args["output"])}
    elif mode == "bind":
        agent, version = db.execute(text("""
            SELECT agent_id, current_version_no FROM nexent.ag_tenant_agent_t WHERE
            tenant_id=:tenant AND name='discipline_case_review' AND version_no=0 AND current_version_no>0 AND delete_flag='N'
        """), args).one()
        result = dict(tenant=tenant, agent=agent, version=version)
    else:
        raise ValueError(mode)
print(json.dumps(result, ensure_ascii=True))
