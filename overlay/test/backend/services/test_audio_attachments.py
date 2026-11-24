# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import ValidationError

from management.services.file import asr


class AudioAttachments(unittest.IsolatedAsyncioTestCase):
    def test_routing_and_service_address(self):
        for f in [{"name": "谈话.M4A"}, {"name": "record", "type": "audio/webm;codecs=opus"}]:
            self.assertTrue(asr.is_audio(f))
        self.assertFalse(asr.is_audio({"name": "材料.pdf"}))
        with self.assertRaises(ValidationError):
            asr.AudioSettings(url="http://example.com:8080")

    def test_time_ranges_and_silence(self):
        self.assertEqual(asr.timestamp(3601.25), "01:00:01.25")
        data = {"duration_seconds": 2, "text": "", "segments": []}
        self.assertEqual(asr.normalized_transcript(data)["segments"], [])
        with self.assertRaises(ValueError):
            asr.normalized_transcript({**data, "text": "缺少时间"})
        with self.assertRaises(ValueError):
            asr.normalized_transcript({**data, "segments": [{"start": 2, "end": 1, "text": "错位"}]})

    async def test_job_status_and_result(self):
        replies = {
            "/v1/jobs/J": {"status": "completed"},
            "/v1/jobs/J/files/json": {"duration_seconds": 4, "segments": [{"start": 1.1, "end": 2.7, "text": "你好。"}]},
        }
        transport = httpx.MockTransport(lambda req: httpx.Response(200, json=replies[req.url.path]))
        async with httpx.AsyncClient(base_url="http://local", transport=transport) as client:
            result = await asr.inspect_job(client, "J")
            self.assertEqual(result["segments"][0]["start"], 1.1)
            for state in ["running", "queued", "failed"]:
                replies["/v1/jobs/J"] = {"status": state}
                r = await asr.inspect_job(client, "J")
                self.assertEqual(r["status"], "error" if state == "failed" else state)

    async def test_scope_and_omissions(self):
        f = {"name":"记录.wav", "object_name":"attachments/u/record"}
        state = {"status":"done", "segments":[{"start":0, "end":1.5, "text":"第一段。"},
                                            {"start":2, "end":4, "text":"长文本"*100}]}
        with patch.object(asr, "validate_s3_url_access") as auth, patch.object(asr, "start_recording", new=AsyncMock(return_value=state)):
            result = await asr.audio_context([f], "t", "u", 2, asr.AudioSettings(), 80)
        auth.assert_called_once_with(f["object_name"], "u", "t")
        self.assertIn("00:00:00.00–00:00:01.50", result)
        self.assertIn("未纳入本轮", result)
