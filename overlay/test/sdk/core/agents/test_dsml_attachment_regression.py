# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
"""Unsupported tool calls must not terminate an attachment review."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from smolagents.utils import AgentExecutionError
from smolagents.agents import EMPTY_PROMPT_TEMPLATES
from smolagents.models import ChatMessage, Model

from nexent.core.agents.core_agent import CoreAgent
from nexent.core.agents.nexent_agent import NexentAgent
from nexent.core.agents.output_protocol import ModelOutputProtocolError, classify_model_output
from nexent.core.agents.sandbox_workspace import SandboxWorkspace
from nexent.core.agents.context import ContextManager
from nexent.core.agents.context.runtime import ManagedContextRuntime
from nexent.core.utils.observer import MessageObserver


def test_provider_markup_in_python_literal_is_data():
    code = 'print("<｜｜DSML｜｜ calls>")'
    assert classify_model_output(f"<code>{code}</code>", protocol="code_action").code == code


def test_agent_continues_after_dsml_instead_of_finishing():
    requests = []

    class ScriptedModel(Model):
        def generate(self, messages, **kwargs):
            requests.append(json.dumps(messages, default=str))
            outputs = ['<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="read_file">',
                       '<code>print("REPORT_READ_OK")</code>',
                       '<final_answer>Review based on REPORT_READ_OK.</final_answer>']
            return ChatMessage(role="assistant", content=outputs[len(requests) - 1])

    templates = deepcopy(EMPTY_PROMPT_TEMPLATES)
    templates["system_prompt"] = "Read and review the attachment using Python."
    agent = CoreAgent(observer=MessageObserver(lang="en"), model=ScriptedModel(model_id="unit"),
                      tools=[], prompt_templates=templates, max_steps=4, verbosity_level=0,
                      context_runtime=ManagedContextRuntime(ContextManager(), []),
                      enable_protocol_repair_retry=False)
    assert str(agent.run("Review attachment")) == "Review based on REPORT_READ_OK."
    assert len(requests) == 3
    assert "executable Python" in requests[1]
    assert "REPORT_READ_OK" in requests[2]


@pytest.mark.parametrize("pipes", ["｜｜", "||"])
@pytest.mark.parametrize("tag", ["calls", 'invoke name="download_from_s3"', 'parameter name="s3_path"'])
def test_dsml_is_recoverable_action_error(pipes, tag):
    text = f"<{pipes}DSML{pipes} {tag}>payload"
    agent = object.__new__(CoreAgent)
    agent._resolve_deferred_model_attempt = Mock()
    agent._known_tool_names = Mock(return_value=["download_from_s3"])
    agent.logger = Mock()
    agent.observer = Mock()
    memory = SimpleNamespace(model_output_message=None)
    with pytest.raises(AgentExecutionError, match="executable"):
        agent._legacy_parse_fallback(memory, text, "stop")
    agent.observer.mark_direct_final_answer.assert_not_called()
    assert not hasattr(memory, "action_output")
    with pytest.raises(ModelOutputProtocolError):
        classify_model_output(text, protocol="code_action")


@pytest.mark.parametrize("text", ["审理意见：需要补充交接记录。", "DSML is a tool format.",
    'Example: `<｜｜DSML｜｜ calls>payload</｜｜DSML｜｜ calls>`'])
def test_legitimate_answer_and_quoted_markup_remain_answers(text):
    agent = object.__new__(CoreAgent)
    agent._resolve_deferred_model_attempt = Mock()
    agent._known_tool_names = Mock(return_value=[])
    agent.observer = Mock()
    result = agent._legacy_parse_fallback(SimpleNamespace(model_output_message=None), text, "stop")
    assert result.is_final_answer and result.output == text


@pytest.mark.parametrize("mapped", [False, True])
def test_downloaded_indexed_paths_are_exposed_as_untrusted_data(tmp_path, mapped):
    agent = object.__new__(NexentAgent)
    agent.workspace_path = str(tmp_path)
    agent.workspace_mapping = SandboxWorkspace(tmp_path, "/mnt/work/run") if mapped else None
    agent.observer = SimpleNamespace(lang="en")
    agent.minio_files = [{"name": "report.txt", "object_name": "a.txt"},
                         {"name": "report.txt", "object_name": "b.txt"}]
    download = Mock()
    download.minio_client.default_bucket = "nexent"
    download.forward.side_effect = lambda source, name: json.dumps({"local_path": str(tmp_path / name)})
    agent.agent = SimpleNamespace(tools={"download_from_s3": download})
    agent._push_file_workspace_to_sandbox = Mock()
    agent._initialize_sandbox_workspaces = Mock()
    query = agent._prepare_file_workspace("Review the report")
    assert "untrusted" in query
    root = "/mnt/work/run" if mapped else tmp_path.as_posix()
    assert f"{root}/inputs/000_report.txt" in query
    assert f"{root}/inputs/001_report.txt" in query
    if mapped:
        assert str(tmp_path) not in query
    assert download.forward.call_count == 2
