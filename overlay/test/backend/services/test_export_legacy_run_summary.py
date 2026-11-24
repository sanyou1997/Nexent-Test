import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from management.services.agent import transfer


@pytest.mark.parametrize(("saved", "expected"), [(None, False), (False, False), (True, True)])
def test_export_run_summary_for_existing_agents(saved, expected):
    row = {
        "tenant_id": "tenant",
        "name": "review",
        "display_name": "Review",
        "description": "Review reports",
        "max_steps": 5,
        "enabled": True,
        "provide_run_summary": saved,
    }
    with (
        patch.object(transfer, "search_agent_info_by_agent_id", return_value=row),
        patch.object(transfer, "query_sub_agents_id_list", return_value=[]),
        patch.object(transfer, "create_tool_config_list", new=AsyncMock(return_value=[])),
        patch.object(transfer.skill_db, "query_skill_instances_by_agent_id", return_value=[]),
    ):
        result = asyncio.run(transfer.export_agent_by_agent_id(1, "tenant", "user"))
    assert result.provide_run_summary is expected
