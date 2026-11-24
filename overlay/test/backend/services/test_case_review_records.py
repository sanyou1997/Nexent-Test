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

    from unittest.mock import Mock
    observer = Mock()

    def verify():
        observer.reset_mock()
        records = asyncio.run(workflow.verified_sources({"url": "local-test"}, [law], observer))
        start, end = observer.add_message.call_args_list
        assert start.args[1] == workflow.ProcessType.TOOL
        assert start.kwargs["tool_name"] == "get_rule"
        assert start.kwargs["tool_arguments"] == {"title": "甲规定（2024年）", "number": 8}
        assert end.args[1] == workflow.ProcessType.EXECUTION_LOGS
        assert end.kwargs["tool_call_id"] == start.kwargs["tool_call_id"]
        return records, end.args[2]

    records, log = verify()
    assert records == {"K8": law} and "已核对" in log
    reply["text"] = "第八条 不同正文。"
    records, log = verify()
    assert not records and "未通过" in log
    reply.update(text=law["body"], url="s3://bucket/attachments/other/rule.txt")
    assert verify()[0] == {}


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
        "## 分项审理意见",
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


def test_brief_retains_verified_alternatives_and_separately_grounded_analysis():
    from management.services.agent.case_review_workflow import render_brief

    rows = {1: ("报告", "移送意见称本人已经签收。"), 2: ("报告", "签收单仅有前台姓名。")}
    evidence = [{"line": 1, "quote": "本人已经签收"}]
    basis = [{"line": 2, "quote": "仅有前台姓名"}]
    brief = {"conclusions": [
        {"title": "签收", "fact_ids": [1], "ref_ids": [1], "text": "签收主体待核", "evidence": evidence,
         "analysis": {"text": "前台代签不能证明本人控制", "evidence": basis}},
        {"text": "移送意见须比对原件", "evidence": evidence,
         "analysis": {"text": "未经证实的扩写", "evidence": [{"line": 2, "quote": "已由本人签字"}]}},
    ]}
    laws = [dict(id=name, title="某规定", num=n, version="某年版", body=body,
                 url=f"s3://source/{name}.txt") for name, n, body in (
        ("original", 2, "第二条 交付事实需要审查。"), ("other", 3, "第三条 签收情况应予核查。"),
        ("unverified", 4, "第四条 未经核实的条文。"))]
    verified = {law["id"]: law for law in laws[:2]}
    excerpts = {("original", 1, "original"): "与原件不同的摘录",
                ("fact", 1, "other"): "签收情况应予核查"}
    output = render_brief(rows, brief, verified, laws, [(1, "某规定", 2, "")], {}, {}, {}, [],
                          {1: [("other", "候选待核", "须核对交付对象"), ("unverified", "候选待核", "无原件")]},
                          law_excerpts=excerpts, facts=[{"evidence": [(2, "仅有前台姓名")]}])
    assert "s3://source/original.txt" in output and "s3://source/other.txt" in output
    assert "s3://source/unverified.txt" not in output
    assert "条文原文：“签收情况应予核查”" in output and "与原件不同的摘录" not in output
    assert "前台代签不能证明本人控制" in output and "未经证实的扩写" not in output


def test_brief_explains_missing_library_without_linking_unverified_law():
    from management.services.agent.case_review_workflow import render_brief

    result = render_brief({1: ("报告", "引用《某规定》第二条")}, {}, {}, [], [(1, "某规定", 2, "")],
                          {}, {}, {}, [], {})
    assert "本轮未读取到可核验的法规原件" in result and "跟随智能体默认" in result
    assert "原件未核实" in result and "s3://" not in result


def test_unusable_summary_reuses_anchored_fact_analysis():
    from management.services.agent.case_review_workflow import render_brief
    rows = {1: ("提交文本", "签收单仅有前台姓名。")}
    summary = {"conclusions": [{"text": "本人签收", "evidence": [{"line": 1, "quote": "本人已签收"}]}]}
    facts = [{"label": "前台代收", "issue": "不能证明本人实际取得",
              "evidence": [(1, "仅有前台姓名")]}]
    result = render_brief(rows, summary, {}, [], [], {}, {}, {}, [], {},
                          report_sources={rows[1][1]: {("", "", 0)}}, facts=facts)
    assert "审理判断" in result and "不能证明本人实际取得" in result
    assert "本人签收" not in result and "#submitted-report" in result

def test_issue_cannot_borrow_another_issues_law_or_drop_unassigned_original():
    from management.services.agent.case_review_workflow import render_brief

    rows = {1: ("材料", "封袋由前台代收。"), 2: ("材料", "报销款由财务支付。"),
            3: ("材料", "报告另引《某规定》第六条。")}
    facts = [{"evidence": [(1, rows[1][1])]}, {"evidence": [(2, rows[2][1])]}]
    laws = [dict(id=str(n), title="某规定", num=n, version="某年版", body="有关事项应予查明。",
                 url=f"s3://source/{n}.txt") for n in (2, 3, 6)]
    summary = {"conclusions": [
        {"title": "封袋交付", "text": "个人取得待核", "fact_ids": [1, 2], "ref_ids": [1],
         "evidence": [{"line": 1, "quote": rows[1][1]}]},
        {"title": "财务付款", "text": "款项性质待核", "fact_ids": [2], "ref_ids": [],
         "evidence": [{"line": 2, "quote": rows[2][1]}]}]}
    result = render_brief(rows, summary, {r["id"]: r for r in laws}, laws,
                          [(3, "某规定", 6, "")], {}, {}, {}, [],
                          {1: [("2", "候选待核", "核对接收人")], 2: [("3", "候选待核", "核对收款人")]},
                          facts=facts)
    first, second = result.split("### 2. 财务付款")
    second, unassigned = second.split("### 原引核查")
    assert "封袋由前台代收" in first and "source/2.txt" in first and "source/3.txt" not in first
    assert "source/3.txt" in second and "source/2.txt" not in second
    assert "source/6.txt" in unassigned and "source/6.txt" not in first + second


def test_original_correction_on_separate_line_stays_with_issue():
    from management.services.agent.case_review_workflow import render_brief
    passage = "移送部门原引《某规定》第六条，审理稿已纠正。"
    rows = {1: ("报告", "前台代签封袋。"), 2: ("报告", passage)}
    item = {"title": "封袋交接", "text": "个人取得待核", "ref_ids": [1],
            "evidence": [{"line": 1, "quote": rows[1][1]}],
            "citation_evidence": [{"line": 2, "quote": passage}]}
    law = dict(id="L", title="某规定", version="某年版", num=6, body="第六条 原文。", url="s3://laws/6.txt")
    args = (rows, {"conclusions": [item]}, {"L": law}, [law], [(2, "某规定", 6, "")],
            {}, {(2, "L"): "引用不符"}, {}, [], {})
    output = render_brief(*args)
    assert "### 原引核查" not in output and "引用不符" in output and "s3://laws/6.txt" in output
    item["citation_evidence"] = [{"line": 2, "quote": "审理稿已纠正"}]
    assert "### 原引核查" in render_brief(*args)


def test_review_plan_events_track_work_and_do_not_complete_cancelled_steps(monkeypatch):
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    import pytest
    from management.services.agent import case_review_workflow as workflow
    from nexent.core.concurrency.cancellation import RunTerminated

    obs = Mock()
    info = SimpleNamespace(observer=obs, agent_config=SimpleNamespace(display_name="审理"))
    def stop_after_facts(info, source, user, connection, instructions, detailed, sources, advance):
        advance("facts")
        raise RunTerminated()
    monkeypatch.setattr(workflow, "_review", stop_after_facts)
    with pytest.raises(RunTerminated):
        workflow.deliver(info, source="", user="u", connection={}, instructions="")
    events = [json.loads(call.args[2]) for call in obs.add_message.call_args_list]
    assert len({event["plan_id"] for event in events}) == 1
    states = [[s["status"] for s in event["steps"]] for event in events]
    assert states == [["in_progress"] + ["pending"] * 5,
                      ["completed", "in_progress"] + ["pending"] * 4,
                      ["completed"] + ["skipped"] * 5]
    assert events[1]["steps"][1]["description"] == "进行中"
    obs.complete_final_answer.assert_not_called()
