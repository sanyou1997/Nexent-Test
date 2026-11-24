# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
from management.services.agent.case_review_records import (
    cited,
    counterpart_candidates,
    direction_mismatch,
    facts_from_plan,
    parse_law,
    period_status,
    source_lines,
    transaction_totals,
)


def test_direction_check_is_bound_to_the_cited_article_and_explicit_claim():
    body = "第十一条 向从事公务的人员赠送财物的，依规定处理。"
    text = "报告以《甲规定》第十一条作为王某收受购物卡的依据。"
    assert "赠送方" in direction_mismatch(text, body, 11)
    assert not direction_mismatch(text, body, 12)
    assert not direction_mismatch("报告列出《甲规定》第十一条，方向须另核。", body, 11)
    original = dict(title="甲规定", version="2024年", num=11, body=body)
    peer = dict(
        title="甲规定",
        version="2024年",
        num=8,
        body="第八条 收受可能影响履职的消费卡。",
    )
    assert counterpart_candidates(text, original, [original, peer]) == [peer]
    assert not counterpart_candidates(
        text, original, [original, peer, dict(peer, num=6)]
    )


def test_verified_sources_rejects_wrong_text_and_foreign_file(monkeypatch):
    import asyncio
    import json
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from management.services.agent import case_review_workflow as workflow

    law = dict(
        id="K8",
        num=8,
        title="甲规定",
        version="2024年",
        effective="2024-01-01",
        url="s3://bucket/attachments/local/rule.txt",
        body="第八条 原件文字。",
    )
    reply = dict(
        id="local-8",
        number=8,
        title=law["title"],
        version=law["version"],
        effective=law["effective"],
        url=law["url"],
        text=law["body"],
        source_check={"ok": True},
    )

    @asynccontextmanager
    async def connection(*args, **kwargs):
        yield None, None

    class Session:
        async def initialize(self):
            pass

        async def call_tool(self, *args):
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))])

    @asynccontextmanager
    async def session(*args):
        yield Session()

    monkeypatch.setattr(workflow, "sse_client", connection)
    monkeypatch.setattr(workflow, "ClientSession", session)

    def verify():
        return asyncio.run(workflow.verified_sources({"url": "local-test"}, [law]))

    assert verify() == {"K8": law}
    reply["text"] = "第八条 不同正文。"
    assert verify() == {}
    reply.update(text=law["body"], url="s3://bucket/attachments/other/rule.txt")
    assert verify() == {}


def test_date_from_unrelated_evidence_cannot_date_an_event():
    lines, tx, _ = source_lines("2025年5月8日办理退休。\n前台登记接收封袋。")
    facts = facts_from_plan(
        lines,
        tx,
        {
            "facts": [
                {
                    "label": "前台接收",
                    "time": {
                        "date": "2025年5月8日",
                        "line": 1,
                        "quote": "2025年5月8日办理退休",
                    },
                    "evidence": [{"line": 2, "quote": "前台登记接收封袋"}],
                }
            ]
        },
    )
    assert facts[0]["when"] == ""
    assert cited(lines, [{"line": 2, "quote": "本人已经收取"}]) == []


def test_duplicate_and_conflicting_transfers():
    header = "<tr><td>流水号</td><td>日期</td><td>金额</td></tr>"
    row = "<tr><td>F-31</td><td>2022年6月1日</td><td>300元</td></tr>"
    lines, tx, _ = source_lines("<table>" + header + row + row + "</table>")
    assert transaction_totals(tx) == (2, 600, 1, 300)
    (f,) = facts_from_plan(lines, tx, {"facts": []})
    assert len(f["evidence"]) == 2 and f["when"] == "2022年6月1日"
    lines, tx, _ = source_lines(
        "<table>" + header + row + row.replace("300元", "500元") + "</table>"
    )
    assert transaction_totals(tx) is None
    assert facts_from_plan(lines, tx, {"facts": []})[0]["conflicting"]


def test_mismatched_article_heading_and_version_boundary():
    source = (
        "施行日期：2024-07-01\n法规定位：《甲规定》；2024年；第三条\n第四条 不同条号。"
    )
    assert not parse_law(source)
    (law,) = parse_law(source.replace("第四条", "第三条"))
    assert "施行在行为之后" in period_status(law, "2023年9月", [law])
    assert "跨施行界限" in period_status(law, "2024年", [law])


def test_brief_keeps_citation_uncertainty_and_rejects_ungrounded_summary():
    from management.services.agent.case_review_workflow import render_brief

    lines = {1: ("报告 · PDF 第5页", "材料仅见签收单，报告引用《甲规定》第八条。")}
    laws = [
        dict(
            id=f"K{i}",
            title="甲规定",
            version=year,
            num=8,
            body="第八条 适用条件。",
            url=f"s3://local/{i}.txt",
        )
        for i, year in enumerate(["2018年", "2023年"])
    ]
    output = render_brief(
        lines,
        {
            "overview": [
                {
                    "text": "已有完整证据链",
                    "evidence": [{"line": 1, "quote": "本人签字"}],
                }
            ]
        },
        {"K1": laws[1]},
        laws,
        [(1, "甲规定", 8, ""), (1, "甲规定", 200, "")],
        {},
        {(1, "K1"): "引用相符"},
        {},
        [],
        {},
    )
    assert [s for s in output.splitlines() if s.startswith("## ")] == [
        "## 总览",
        "## 审理结论",
        "## 引用核查",
        "## 办理建议",
    ]
    assert "版本待核" in output and "**适用待核**" in output
    assert "授权原件中未查到" in output and "第200条" in output
    assert "已有完整证据链" not in output and "s3://local/0.txt" not in output


def test_brief_excerpt_uses_original_spacing_and_the_authorized_page():
    from urllib.parse import parse_qs
    from management.services.agent.case_review_workflow import report_excerpt
    row = "项目组建议先作处理，退款 5,400 元仍待核对。"
    lines = {3: ("结算.pdf · PDF 第5页", row)}
    target = ("attachments/owner/report.pdf", "结算.pdf", 5)
    sources = {row: {target, (target[0], target[1], 2)}}
    seen = set()
    excerpt = report_excerpt(lines, [(3, "退款5,400元仍待核对")], sources, seen)
    assert "[退款 5,400 元仍待核对]" in excerpt
    query = excerpt.split("#report-source?", 1)[1].split(")", 1)[0]
    assert parse_qs(query) == {"object":[target[0]], "name":["结算.pdf"], "page":["5"]}
    assert report_excerpt(lines, [(3, "退款5,400元仍待核对")], sources, seen) == ""
    assert not report_excerpt(lines, [(3, "报告确认退款全部属实")], sources, set())
    sources[row].add(("attachments/other/second.pdf", "结算.pdf", 5))
    assert "#report-source" not in report_excerpt(lines, [(3, row)], sources, set())


def test_brief_pasted_report_and_nonmatching_page():
    from management.services.agent.case_review_workflow import report_excerpt
    row = "事实尚待核实。"
    evidence = [(1, row)]
    assert "#submitted-report" in report_excerpt({1: ("提交文本", row)}, evidence, {row: {("", "", 0)}}, set())
    actual = {row: {("attachments/user/report.pdf", "报告.pdf", 1)}}
    assert "#report-source" not in report_excerpt({1: ("报告.pdf · PDF 第2页", row)}, evidence, actual, set())
