# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
import base64
import json
import unittest
from io import BytesIO
from unittest.mock import AsyncMock, patch

import httpx
from pypdf import PdfReader, PdfWriter
from pydantic import ValidationError

from management.services.file import asr, local_ocr, mineru, parser_providers as providers


class ProvidersTest(unittest.IsolatedAsyncioTestCase):
    def test_local_only_and_tenant_profiles(self):
        saved = {"pdf_provider": "tencent_ocr", "audio_provider": "volc_asr", "credential_volc_asr_API Key": "old"}
        view = providers.catalog(saved)
        self.assertEqual(view["active"], {"pdf":"mineu","audio":"paraformer"})
        self.assertNotIn("credential_", str(view))
        for kind, name in (("audio","volc_asr"), ("pdf","tencent_ocr"), ("audio","umi_ocr")):
            with self.assertRaises(ValueError):
                providers.prepare(saved, kind, providers.ProviderRequest(provider=name))
        _, updates = providers.prepare({}, "pdf", providers.ProviderRequest(
            provider="umi_ocr", local={"url":"http://nexent-umi-ocr:1224"}))
        with patch.object(providers, "load_settings", side_effect=lambda tenant: updates if tenant=="a" else saved):
            self.assertEqual(providers.selected_config("a","pdf").url,"http://nexent-umi-ocr:1224")
            self.assertIsInstance(providers.selected_config("b","pdf"), mineru.ParserSettings)
        self.assertEqual(providers.catalog(updates)["urls"]["umi_ocr"],"http://nexent-umi-ocr:1224")
        for invalid in ("https://example.com:443","http://user@host.docker.internal:18124","http://host.docker.internal:18124/api/ocr"):
            with self.assertRaises(ValidationError):
                providers.LocalOCR(provider="umi_ocr",url=invalid)

    async def test_native_protocols_preserve_page_order(self):
        writer = PdfWriter()
        for width in (300, 450):
            writer.add_blank_page(width, 400)
        buffer = BytesIO()
        writer.write(buffer)
        real_client = httpx.AsyncClient
        for name in providers.OCR_DEFAULT_URLS:
            config, _ = providers.prepare({}, "pdf", providers.ProviderRequest(provider=name))
            seen = []
            def reply(req):
                self.assertEqual(req.url.path,local_ocr.PATHS[name])
                form = json.loads(req.content)
                if name == "umi_ocr":
                    from PIL import Image
                    width = Image.open(BytesIO(base64.b64decode(form["base64"]))).width / 2.5
                    payload = {"code":101} if width==300 else {"code":100,"data":"page two"}
                else:
                    self.assertEqual(form["fileType"],0)
                    pages = PdfReader(BytesIO(base64.b64decode(form["file"]))).pages
                    self.assertEqual(len(pages),1)
                    width = pages[0].mediabox.width
                    content = "" if width==300 else "page two"
                    result = ({"ocrResults":[{"prunedResult":{"rec_texts":[content]}}]} if name=="paddle_ocr"
                              else {"layoutParsingResults":[{"markdown":{"text":content}}]})
                    payload = {"errorCode":0,"result":result}
                seen.append(width)
                return httpx.Response(200,json=payload)
            with patch.object(local_ocr.httpx, "AsyncClient", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(reply),**kw)):
                self.assertEqual(await mineru.parse_pdf(buffer.getvalue(),config),["","page two"])
                self.assertEqual(await mineru.parse_pdf(buffer.getvalue(),config,single_page=2),["page two"])
            self.assertEqual(seen,[300,450,450])

    def test_malformed_pages(self):
        for provider, payload in (("umi_ocr",{"code":500}), ("paddle_ocr", {"errorCode":1}),
                                  ("paddle_layout",{"errorCode":0,"result":{"layoutParsingResults":[]}})):
            with self.assertRaises(ValueError):
                local_ocr.page_text(provider,payload)


    async def test_qwen_text_only_never_invents_timestamps(self):
        config, changes = providers.prepare({}, "audio", providers.ProviderRequest(provider="qwen_asr"))
        self.assertEqual(providers.catalog(changes)["active"]["audio"], "qwen_asr")
        self.assertEqual(providers.catalog({})["active"]["audio"], "paraformer")
        with self.assertRaises(ValidationError):
            providers.QwenASR(url="https://example.net")
        original_client = httpx.AsyncClient
        def reply(req):
            self.assertEqual(req.url.path, "/v1/audio/transcriptions")
            self.assertIn(config.model.encode(), req.content)
            return httpx.Response(200, json={"text":"录音正文"})
        with patch.object(asr.httpx, "AsyncClient", side_effect=lambda **kw: original_client(transport=httpx.MockTransport(reply), **kw)):
            result = await asr.transcribe_qwen(b"sample", "proof.wav", config)
        self.assertEqual(result, {"segments": [], "transcript": "录音正文"})
        with patch.object(asr, "start_recording", new=AsyncMock(return_value={"status":"done", **result})):
            context = await asr.audio_context([{"object_name":"attachments/u/proof.wav", "name":"proof.wav"}], "tenant", "u", 1, config, 100)
        self.assertIn("录音正文", context)
        self.assertIn("服务未提供分段时间戳", context)
        self.assertNotIn("00:00:", context)
