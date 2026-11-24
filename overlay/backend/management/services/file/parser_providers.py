# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
"""Resolve self-hosted attachment parsers from the authenticated tenant."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from consts.const import LOCAL_OCR_ALLOWED_ORIGINS, QWEN_ASR_ALLOWED_ORIGINS
from database.document_parser_db import load_settings

OCR_DEFAULT_URLS = {
    "paddle_ocr": "http://host.docker.internal:8080",
    "paddle_layout": "http://host.docker.internal:8081",
    "umi_ocr": "http://nexent-umi-ocr:1224",
}
DEFAULTS = {"pdf": "mineu", "audio": "paraformer"}
RETIRED = {"pdf": {"baidu_ocr", "tencent_ocr"}, "audio": {"tencent_asr", "volc_asr"}}


def allowed_origin(value, origins):
    url = value.strip().rstrip("/").lower()
    parsed = urlsplit(url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment or url not in origins):
        raise ValueError("请使用部署端允许的服务根地址")
    return url


class LocalOCR(BaseModel, extra="forbid"):
    provider: Literal["paddle_ocr", "paddle_layout", "umi_ocr"]
    url: str

    @field_validator("url")
    @classmethod
    def approved_origin(cls, value):
        return allowed_origin(value, LOCAL_OCR_ALLOWED_ORIGINS)


QWEN_URL = "http://nexent-qwen-asr:8000"
QWEN_MODEL = "Qwen/Qwen3-ASR-0.6B"


class QwenASR(BaseModel, extra="forbid"):
    provider: Literal["qwen_asr"] = "qwen_asr"
    url: str = Field(default=QWEN_URL, validate_default=True)
    model: str = Field(default=QWEN_MODEL, min_length=1, max_length=200, pattern=r"^[\w./:-]+$")

    @field_validator("url", mode="after")
    @classmethod
    def approved_origin(cls, value):
        return allowed_origin(value, QWEN_ASR_ALLOWED_ORIGINS)


class ProviderRequest(BaseModel, extra="forbid"):
    provider: str
    local: dict = Field(default_factory=dict)


def active_provider(saved, kind):
    value = saved.get(kind + "_provider", DEFAULTS[kind])
    return DEFAULTS[kind] if value in RETIRED[kind] else value


def catalog(saved):
    qwen = saved.get("local_qwen_asr", {})
    return {"active": {kind: active_provider(saved, kind) for kind in DEFAULTS},
            "urls": {**{key: saved.get("local_" + key, {}).get("url", url) for key, url in OCR_DEFAULT_URLS.items()},
                     "qwen_asr": qwen.get("url", QWEN_URL)},
            "qwen_model": qwen.get("model", QWEN_MODEL), "qwen_origins": sorted(QWEN_ASR_ALLOWED_ORIGINS),
            "allowed_origins": sorted(LOCAL_OCR_ALLOWED_ORIGINS)}


def prepare(saved, kind, request):
    from management.services.file.asr import AudioSettings
    from management.services.file.mineru import ParserSettings

    provider = request.provider
    changes = {kind + "_provider": provider}
    if (kind, provider) == ("pdf", "mineu"):
        config = ParserSettings(**request.local)
        changes.update(config.model_dump())
    elif (kind, provider) == ("audio", "paraformer"):
        config = AudioSettings(**request.local)
        changes["asr"] = config.model_dump()
    elif (kind, provider) == ("audio", "qwen_asr"):
        config = QwenASR(**(request.local or saved.get("local_qwen_asr", {})))
        changes["local_qwen_asr"] = config.model_dump(exclude={"provider"})
    elif kind == "pdf" and provider in OCR_DEFAULT_URLS:
        profile = request.local or saved.get("local_" + provider) or {"url": OCR_DEFAULT_URLS[provider]}
        config = LocalOCR(provider=provider, **profile)
        changes["local_" + provider] = {"url": config.url}
    else:
        raise ValueError("请选择支持该文件类型的本地服务")
    return config, changes


def selected_config(tenant, kind):
    from management.services.file.mineru import ParserSettings
    from management.services.file.asr import AudioSettings

    saved = load_settings(tenant)
    provider = active_provider(saved, kind)
    if provider == "mineu" and kind == "pdf":
        return ParserSettings(**{key: saved[key] for key in ParserSettings.model_fields if key in saved})
    if provider == "paraformer" and kind == "audio":
        return AudioSettings(**saved.get("asr", {}))
    return prepare(saved, kind, ProviderRequest(provider=provider))[0]
