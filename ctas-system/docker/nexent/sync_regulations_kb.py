"""把业务平台法规库同步进 Nexent 知识库（在 nexent-config 容器内运行，走 Nexent 自身的上传 / 入库服务函数）。

平台法规库是主本，Nexent 知识库是检索副本（单向同步）：
- 知识库不存在则创建（向量模型沿用租户当前的 Embedding 配置，或 --embedding-model-id 指定）；
- 逐文件比对「同步指纹」：新增 / 内容有变化的重新上传入库，主本已下架的从知识库删除，未变化的跳过；
- 入库走 Nexent 标准流水线（MinIO + data-process，按标题切块，每条法规一块并带条号）。

用法见 sync_regulations_kb.sh；单独运行：
  docker exec -w /opt/backend -e PYTHONPATH=/opt/backend nexent-config \
      python /tmp/sync_regulations_kb.py --email <租户管理员邮箱> --dir /tmp/ctas_kb [--kb 纪检监察业务平台法规库] [--wait 600]
"""
import argparse
import asyncio
import io
import json
import os
import time
from pathlib import Path

import requests
from sqlalchemy import func, select
from starlette.datastructures import Headers, UploadFile

from consts.const import VectorDatabaseType
from consts.model import ProcessParams
from consts.task_recovery import CONFIG_SERVICE_NAME
from database.client import get_db_session
from database.db_models import KnowledgeRecord, UserTenant
from management.services.file.service import upload_files_impl
from management.services.knowledge_base.ingestion import trigger_data_process
from management.services.knowledge_base.service import ElasticSearchService, get_vector_db_core
from management.services.knowledge_base.task_state import get_redis_service
from management.services.permission.identity.authentication_helpers import generate_session_jwt
from management.services.configuration.tenant.settings import tenant_config_manager

ES = os.environ.get("ELASTICSEARCH_HOST", "http://nexent-elasticsearch:9200")
ES_HEADERS = {"Authorization": "ApiKey " + os.environ["ELASTICSEARCH_API_KEY"]} if os.environ.get("ELASTICSEARCH_API_KEY") else {}


def es_count(index: str, query: dict) -> int:
    r = requests.post(f"{ES}/{index}/_count", headers=ES_HEADERS, json={"query": query}, timeout=15, verify=False)
    return r.json().get("count", 0) if r.ok else 0


def chunks_of(index: str, filename: str) -> int:
    return es_count(index, {"term": {"filename": filename}})


def has_fingerprint(index: str, filename: str, fp: str) -> bool:
    return es_count(index, {"bool": {"filter": [{"term": {"filename": filename}}],
                                     "must": [{"match_phrase": {"content": f"同步指纹 {fp}"}}]}}) > 0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--dir", default="/tmp/ctas_kb")
    ap.add_argument("--kb", default="纪检监察业务平台法规库")
    ap.add_argument("--embedding-model-id", type=int, default=None)
    ap.add_argument("--wait", type=int, default=600, help="等待入库完成的秒数（0 不等待）")
    a = ap.parse_args()

    src = Path(a.dir)
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))

    with get_db_session() as db:
        u = db.scalars(select(UserTenant).where(func.lower(UserTenant.user_email) == a.email.lower(),
                                                UserTenant.delete_flag == "N")).one()
        uid, tid = u.user_id, u.tenant_id
        rec = db.scalars(select(KnowledgeRecord).where(KnowledgeRecord.tenant_id == tid,
                                                       KnowledgeRecord.knowledge_name == a.kb,
                                                       KnowledgeRecord.delete_flag != "Y")).first()
        index = rec.index_name if rec else None

    vdb = get_vector_db_core(db_type=VectorDatabaseType.ELASTICSEARCH)
    created = False
    if not index:
        emb_id = a.embedding_model_id or (tenant_config_manager.get_model_config("EMBEDDING_ID", tenant_id=tid) or {}).get("model_id")
        res = ElasticSearchService.create_knowledge_base(
            knowledge_name=a.kb, embedding_dim=None, vdb_core=vdb, user_id=uid, tenant_id=tid,
            ingroup_permission="READ_ONLY", embedding_model_id=emb_id, preserve_source_file=True)
        index, created = res["id"], True

    listed = await ElasticSearchService.list_files(index, include_chunks=False, vdb_core=vdb)
    existing: dict[str, list[str]] = {}
    for f in listed.get("files", []):
        existing.setdefault(f.get("file") or f.get("filename") or "", []).append(f.get("path_or_url"))

    to_upload, skipped, removed = [], [], []
    for name, meta in manifest.items():
        if name in existing and has_fingerprint(index, name, meta["fingerprint"]):
            skipped.append(name)
            continue
        to_upload.append(name)

    async def drop(name: str):
        for path in existing.get(name, []):
            if not path:
                continue
            await ElasticSearchService.delete_document_by_scope(index, path, "full", vdb)
            try:
                get_redis_service().delete_document_records(index, path)
            except Exception:
                pass

    for name in list(existing):
        if name and name not in manifest:  # 主本已下架 / 改名
            await drop(name)
            removed.append(name)
    for name in to_upload:
        await drop(name)

    tasks = None
    if to_upload:
        files = [UploadFile(file=io.BytesIO((src / n).read_bytes()), filename=n,
                            headers=Headers({"content-type": "text/markdown"})) for n in to_upload]
        errors, paths, names = await upload_files_impl("minio", files, None, index, uid,
                                                       uploader_tenant_id=tid, upload_owner_service=CONFIG_SERVICE_NAME)
        if errors:
            print(json.dumps({"upload_errors": errors}, ensure_ascii=False))
        params = ProcessParams(chunking_strategy="by_title", source_type="minio", index_name=index,
                               authorization=generate_session_jwt(uid))
        tasks = await trigger_data_process([{"path_or_url": p, "filename": n} for p, n in zip(paths, names)], params)

    pending = set(to_upload)
    t0 = time.time()
    while pending and a.wait and time.time() - t0 < a.wait:
        await asyncio.sleep(5)
        pending = {n for n in pending if not has_fingerprint(index, n, manifest[n]["fingerprint"])}

    print(json.dumps(dict(
        tenant=tid[:8], kb=a.kb, index=index, created=created,
        uploaded=len(to_upload), skipped=len(skipped), removed=removed,
        still_processing=sorted(pending), task_submit=("ok" if tasks else None) if to_upload else "n/a",
        chunks={n: chunks_of(index, n) for n in manifest},
    ), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
