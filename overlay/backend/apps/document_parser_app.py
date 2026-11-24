# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
import re
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database.document_parser_db import load_settings, save_settings
from management.services.file.asr import AudioSettings, inspect_job, refresh_recording, submit, transcribe_qwen
from management.services.file.access import validate_s3_url_access
from management.services.file.mineru import (
    ParserSettings, doc_key, parse_pdf, pdf_settings, process_attachment, read_state,
)
from management.services.file.parser_providers import ProviderRequest, QwenASR, catalog, prepare, selected_config
from permissions.depends import authenticate
from permissions.models import CurrentUser

router = APIRouter(prefix="/document_parsers", tags=["document parsing"])


def administrator(user: CurrentUser = Depends(authenticate)):
    if user.normalized_role not in {"ADMIN", "SU", "SPEED"}:
        raise HTTPException(403, "仅租户管理员可以修改解析服务")
    return user


class AgentSwitch(BaseModel):
    enabled: bool | None = None
    audio_enabled: bool | None = None


class AttachmentQuery(BaseModel):
    object_name: str = Field(min_length=1, max_length=1024)


class RetryPage(AttachmentQuery):
    page: int = Field(ge=1)


@router.get("/settings")
def settings(user=Depends(administrator)):
    return pdf_settings(user.tenant_id)


@router.put("/settings")
def update_settings(config: ParserSettings, user=Depends(administrator)):
    save_settings(user.tenant_id, 0, config.model_dump(), user.user_id)
    return config


@router.post("/test_connection")
async def connection(config: ParserSettings, user=Depends(administrator)):
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.get(config.url + "/health")
            response.raise_for_status()
            data = response.json()
            if data.get("status") != "healthy" or "protocol_version" not in data:
                raise ValueError()
            return {"message": "MineU 服务正常", "version": data.get("version")}
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "无法连接到可用的 MineU 服务")


@router.post("/sample")
async def sample(config: ParserSettings, user=Depends(administrator)):
    fixture = Path(__file__).resolve().parents[1] / "assets" / "mineru_check.pdf"
    try:
        pages = await parse_pdf(fixture.read_bytes(), config)
    except ValueError:
        raise HTTPException(502, "无法读取试解析样本或服务返回结果") from None
    except Exception:
        raise HTTPException(502, "试解析失败，请检查地址、模型及解析服务状态")
    amounts = re.findall(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d.,])", pages[1]) if len(pages) == 2 else []
    return {"pages": pages, "verified": {"6000.00", "8000.00", "14000.00"}.issubset(
        amount.replace(",", "") for amount in amounts)}


async def agent_permission(user, agent_id):
    from consts.const import CAN_EDIT_ALL_USER_ROLES
    from database.group_db import query_group_ids_by_user
    from management.services.tag.library import AuthenticatedCaller, TagManagementService

    caller = AuthenticatedCaller(user.user_id, user.tenant_id, user.role,
                                 query_group_ids_by_user(user.user_id) or (),
                                 user.normalized_role in CAN_EDIT_ALL_USER_ROLES)
    try:
        await TagManagementService._resolve_assignment_resource(
            caller, "agent", str(agent_id), require_edit=True, provider=None, knowledge_base_id=None)
    except Exception:
        raise HTTPException(403, "没有此智能体的编辑权限")


@router.get("/asr/settings")
def asr_settings(user=Depends(administrator)):
    return AudioSettings(**load_settings(user.tenant_id).get("asr", {}))


@router.put("/asr/settings")
def update_asr_settings(config: AudioSettings, user=Depends(administrator)):
    save_settings(user.tenant_id, 0, {"asr": config.model_dump()}, user.user_id)
    return config


@router.post("/asr/test_connection")
async def asr_connection(config: AudioSettings, user=Depends(administrator)):
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.get(config.url + "/health")
            response.raise_for_status()
            health = response.json()
            if health.get("status") != "ready" or not {"Paraformer-Large", "FSMN-VAD", "CT-Punc"}.issubset(health.get("models", [])):
                raise ValueError()
            return {"runtime": health["runtime"], "models": health["models"]}
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(502, "无法连接到已就绪的本地普通话转写服务")


@router.post("/asr/sample")
async def asr_sample(config: AudioSettings, user=Depends(administrator)):
    import asyncio
    fixture = Path(__file__).resolve().parents[1] / "assets" / "asr_check.wav"
    try:
        async with httpx.AsyncClient(base_url=config.url, timeout=20, trust_env=False) as client:
            job = await submit(client, fixture.read_bytes(), "sample.wav")
            async with asyncio.timeout(90):
                while True:
                    result = await inspect_job(client, job)
                    if result["status"] == "done":
                        text = "".join(s["text"] for s in result["segments"])
                        return {**result, "verified": all(v in text for v in ("核对录音", "上午九点"))}
                    if result["status"] == "error":
                        raise ValueError()
                    await asyncio.sleep(1)
    except (httpx.HTTPError, ValueError, KeyError, TimeoutError):
        raise HTTPException(502, "试转写未完成，请检查服务或稍后重试")


@router.get("/providers")
def providers(user=Depends(administrator)):
    return catalog(load_settings(user.tenant_id))


def prepared(kind, body, user):
    try:
        return prepare(load_settings(user.tenant_id), kind, body)
    except ValueError as exc:
        from pydantic import ValidationError
        detail = "请填写部署端允许的服务根地址，并检查解析参数" if isinstance(exc, ValidationError) else str(exc)
        raise HTTPException(422, detail) from None


@router.put("/providers/{kind}")
def update_provider(kind: Literal["pdf", "audio"], body: ProviderRequest, user=Depends(administrator)):
    _, changes = prepared(kind, body, user)
    save_settings(user.tenant_id, 0, changes, user.user_id)
    return providers(user)


@router.post("/providers/{kind}/sample")
async def provider_sample(kind: Literal["pdf", "audio"], body: ProviderRequest, user=Depends(administrator)):
    config, _ = prepared(kind, body, user)
    if kind == "pdf":
        return await sample(config, user)
    if isinstance(config, QwenASR):
        fixture = Path(__file__).resolve().parents[1] / "assets" / "asr_check.wav"
        try:
            result = await transcribe_qwen(fixture.read_bytes(), "sample.wav", config, deadline=120)
        except (httpx.HTTPError, KeyError, TypeError, ValueError, TimeoutError):
            raise HTTPException(502, "Qwen ASR 试转写失败，请核对内网服务、模型名及服务状态") from None
        text = result.get("transcript") or "".join(s["text"] for s in result["segments"])
        return {**result, "verified": "核对录音" in text and ("上午九点" in text or "上午9点" in text)}
    return await asr_sample(config, user)


@router.get("/agents/{agent_id}")
async def agent_settings(agent_id: int, user=Depends(authenticate)):
    await agent_permission(user, agent_id)
    saved = load_settings(user.tenant_id, agent_id)
    return {k: bool(saved.get(k)) for k in ("enabled", "audio_enabled")}


@router.put("/agents/{agent_id}")
async def update_agent_settings(agent_id: int, body: AgentSwitch, user=Depends(authenticate)):
    await agent_permission(user, agent_id)
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(422, "请选择一种文件解析类型")
    save_settings(user.tenant_id, agent_id, fields, user.user_id)
    saved = load_settings(user.tenant_id, agent_id)
    return {k: bool(saved.get(k)) for k in ("enabled", "audio_enabled")}


def authorized_state(user, object_name):
    try:
        validate_s3_url_access(object_name, user.user_id, user.tenant_id)
    except (PermissionError, ValueError):
        raise HTTPException(403, "无法访问附件")
    return read_state(doc_key(user.tenant_id, user.user_id, object_name))


@router.post("/attachment")
async def attachment(body: AttachmentQuery, user=Depends(authenticate)):
    state = authorized_state(user, body.object_name)
    if state.get("kind") == "audio":
        try:
            state = await refresh_recording(doc_key(user.tenant_id, user.user_id, body.object_name), state)
        except httpx.HTTPError:
            raise HTTPException(502, "暂时无法查询转写服务，请稍后重新打开")
        return {k: state[k] for k in ("kind", "status", "duration", "segments", "transcript", "message") if k in state}
    return {"status": state.get("status", "waiting"), "pages": state.get("pages", [])}


@router.post("/retry_page")
async def retry_page(body: RetryPage, user=Depends(authenticate)):
    state = authorized_state(user, body.object_name)
    agent = state.get("agent_id")
    if not agent or not load_settings(user.tenant_id, agent).get("enabled"):
        raise HTTPException(409, "当前附件没有启用的解析任务")
    if not 1 <= body.page <= len(state.get("pages", [])):
        raise HTTPException(400, "请在整份解析完成后选择有效页码")
    try:
        result = await process_attachment(user.tenant_id, user.user_id, agent, body.object_name,
                                          selected_config(user.tenant_id, "pdf"), body.page)
    except Exception:
        raise HTTPException(502, "该页重新识别失败，请检查解析服务及当前配置")
    return {"status": result["status"], "pages": result["pages"]}
