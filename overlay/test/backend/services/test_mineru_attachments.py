# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from management.services.file import mineru


class DocumentParserTests(unittest.IsolatedAsyncioTestCase):
    def test_physical_pages_and_table(self):
        blocks = [{"type": "text", "page_idx": 0, "text": "报告"},
                  {"type": "table", "page_idx": 2, "table_body": "<table>8000</table>"}]
        self.assertEqual(mineru.page_texts(json.dumps(blocks), 3), ["报告", "", "<table>8000</table>"])
        with self.assertRaises(ValueError):
            mineru.page_texts(blocks, 2)

    def test_configuration(self):
        self.assertEqual(mineru.ParserSettings().mode, "auto")
        for url in ["http://example.net:80", "http://u:p@host.docker.internal:80",
                    "http://host.docker.internal:18001/health"]:
            with self.subTest(url=url), self.assertRaises(ValidationError):
                mineru.ParserSettings(url=url)

    def test_cache_isolation(self):
        key = mineru.doc_key("A", "alice", "example")
        for tenant, user in [("B", "alice"), ("A", "bob")]:
            self.assertNotEqual(key, mineru.doc_key(tenant, user, "example"))

    async def test_disabled_agent(self):
        with patch.object(mineru, "load_settings", return_value={}), patch.object(mineru, "process_attachment") as worker:
            result = await mineru.attachment_context([{"name":"a.pdf"}], "t", "u", 1, "review")
            self.assertEqual(result, "")
            worker.assert_not_called()

    async def test_missing_page_is_visible(self):
        doc = {"name":"a.pdf", "object_name":"attachments/u/a.pdf"}
        with patch.object(mineru, "load_settings", return_value={"enabled": True}), patch.object(
            mineru, "selected_config", return_value=mineru.ParserSettings()
        ), patch.object(
            mineru, "process_attachment", new=AsyncMock(return_value={"pages":["金额 6,000", "", "金额 8,000"]})
        ):
            result = await mineru.attachment_context([doc], "t", "u", 1, "review")
        self.assertIn("原 PDF 共 3 页", result)
        self.assertIn("第 2 页：未提取到文字", result)
        self.assertIn("PDF 第 3 页】\n金额 8,000", result)
