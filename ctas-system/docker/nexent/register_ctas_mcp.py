"""在 Nexent 中注册业务平台 MCP 服务（ctas_tools），并把其工具同步进租户工具目录（在 nexent-config 容器内运行）。

用法（令牌经环境变量传入，不出现在命令行历史里）：
  docker cp docker/nexent/register_ctas_mcp.py nexent-config:/tmp/
  docker exec -w /opt/backend -e PYTHONPATH=/opt/backend -e CTAS_MCP_TOKEN=<与 ctas-system/.env 一致> \
      nexent-config python /tmp/register_ctas_mcp.py --email <租户管理员邮箱> [--url http://ctas-mcp:8097/sse]

幂等：已注册则更新地址与令牌并重新拉取工具清单；工具清单以 ToolInfo.usage == 服务名 落库，
随后运行 install_scene_agents.py --mcp ctas_tools 即可把工具绑定给「纪检研判助手」。
"""
import argparse
import asyncio
import json
import os

from sqlalchemy import func, select, update

from database.client import filter_property, get_db_session
from database.db_models import McpRecord, ToolInfo, UserTenant
from database.remote_mcp_db import check_mcp_name_exists
from management.services.mcp.registration import add_mcp_service
from management.services.tool.discovery import get_tool_from_remote_mcp_server

DESC = "纪检监察业务平台：案件 / 信访 / 审理查询，量纪说理、证据链与程序合规校验，法规引用核对，历史报告类案检索（只读）"


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", default="ctas_tools")
    ap.add_argument("--url", default="http://ctas-mcp:8097/sse")
    a = ap.parse_args()
    token = os.environ.get("CTAS_MCP_TOKEN", "").strip()
    if not token:
        raise SystemExit("缺少环境变量 CTAS_MCP_TOKEN")
    auth = f"Bearer {token}"

    with get_db_session() as db:
        u = db.scalars(select(UserTenant).where(func.lower(UserTenant.user_email) == a.email.lower(),
                                                UserTenant.delete_flag == "N")).one()
        uid, tid = u.user_id, u.tenant_id

    created = False
    if not check_mcp_name_exists(a.name, tid):
        await add_mcp_service(tenant_id=tid, user_id=uid, name=a.name, description=DESC, source="local",
                              server_url=a.url, tags=["纪检监察", "业务平台"], authorization_token=auth,
                              container_config=None, registry_json=None, enabled=True, ingroup_permission="PRIVATE")
        created = True
    else:
        with get_db_session() as db:
            db.execute(update(McpRecord).where(McpRecord.tenant_id == tid, McpRecord.mcp_name == a.name,
                                               McpRecord.delete_flag == "N")
                       .values(mcp_server=a.url, authorization_token=auth, status=True, updated_by=uid))

    tools = await get_tool_from_remote_mcp_server(a.name, a.url, tid, authorization_token=auth)
    names = sorted(t.name for t in tools)
    with get_db_session() as db:
        existing = {r.name: r for r in db.scalars(select(ToolInfo).where(
            ToolInfo.author == tid, ToolInfo.usage == a.name, ToolInfo.delete_flag == "N")).all()}
        for t in tools:
            data = filter_property(t.__dict__, ToolInfo)
            row = existing.pop(t.name, None)
            if row:
                for k, v in data.items():
                    if k not in ("tool_id", "author", "created_by"):
                        setattr(row, k, v)
                row.is_available, row.updated_by = True, uid
            else:
                data.update(author=tid, is_available=True, created_by=uid, updated_by=uid, delete_flag="N")
                db.add(ToolInfo(**data))
        for gone in existing.values():  # 服务端已下线的工具
            gone.is_available = False
        db.flush()
        ids = {r.name: r.tool_id for r in db.scalars(select(ToolInfo).where(
            ToolInfo.author == tid, ToolInfo.usage == a.name, ToolInfo.delete_flag == "N",
            ToolInfo.is_available.is_(True))).all()}
    print(json.dumps(dict(tenant=tid[:8], mcp=a.name, created=created, url=a.url, tools=names, tool_ids=ids),
                     ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
