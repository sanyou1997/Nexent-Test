"""在 Nexent 中安装 / 更新业务平台的「场景智能体」（在 nexent-config 容器内运行，使用 Nexent 自身服务函数）。

用法：
  docker cp docker/nexent/install_scene_agents.py nexent-config:/tmp/
  docker exec -w /opt/backend nexent-config python /tmp/install_scene_agents.py --email <租户管理员邮箱> \
      [--kb 纪检监察业务平台法规库] [--mcp ctas_tools]

前置：sync_regulations_kb.sh（平台法规库 → Nexent 知识库）、register_ctas_mcp.py（平台 MCP 工具）。
幂等：同名智能体存在则更新提示词、工具绑定并发布新版本；不在清单里的旧工具绑定会被停用。
模型取租户默认 LLM（在 Nexent 里可按智能体改）。
"""
import argparse
import json
from types import SimpleNamespace

from sqlalchemy import func, select

from database.client import get_db_session
from database.db_models import ToolInfo, ToolInstance, UserTenant
from database.agent_db import create_agent, find_agent_id_by_agent_name, update_agent
from database.knowledge_db import get_knowledge_record
from database.tool_db import create_or_update_tool_by_tool_info
from management.services.agent.versions import publish_version_impl
from management.services.configuration.tenant.settings import tenant_config_manager

COMMON = (
    "你服务于纪检监察业务辅助平台。平台会把任务说明、业务数据和输出格式一并放在用户消息里，严格按其执行。"
    "铁律：不得编造事实、数字、人名、单位、日期和法规条款；材料里没有的信息明确说明「材料未载明」；"
    "涉及定性、处分档次等须由审理组集体审议的结论，只给依据与参考，不下结论。"
)

AGENTS = [
    {
        "name": "ctas_extract", "display_name": "要素抽取助手",
        "description": "从信访件、报告、表格表头等原文中抽取结构化要素，按要求输出 JSON。",
        "prompt": COMMON + "你是结构化抽取助手：要求 JSON 时只输出一个 JSON 对象，不要代码块、不要任何解释。"
                  "JSON 缺失值规则优先于上文「材料未载明」的写法：原文没有的字段一律填空字符串或空数组，不写「材料未载明」「不详」等说明，"
                  "也不在字段值里加括号注释或推断；原文只有泛称（如「个别干部」「某局」）时照录泛称。"
                  "字段给出可选值时只能从可选值中选；未给可选值的字段只摘取原文表述，不自行定性归类。",
        # 纯抽取不需要深度思考：关闭后时延明显下降（DeepSeek 由 Nexent 转成 thinking.type=disabled）
        "tools": [], "temperature": 0.0, "max_steps": 3, "extra_params": {"enable_thinking": False},
        "greeting": "请粘贴原文并说明要抽取的字段。",
    },
    {
        "name": "ctas_writer", "display_name": "文书起草助手",
        "description": "按模板与要素起草、润色、审查纪检监察文书，整理谈话纪要；保持事实与数字不变。",
        "prompt": COMMON + "你是文书起草、润色与审查助手：使用规范的党政机关公文与纪检监察文书语言；只改表述不改事实；审查时指出连贯性、逻辑矛盾与前后不一致之处；按要求输出，不加多余寒暄。"
                  "要素未提供的栏目或内容（如后续工作安排）不得自拟事实、措施或评价，写「材料未载明」，确需给出的写法标注「（建议）」；"
                  "不改变原有表述的强弱程度（如「部分问题回避」不得写成「未如实说明」）。",
        "tools": [], "temperature": 0.2, "max_steps": 3,
        "greeting": "请说明文书类型并提供要素或原稿。",
    },
    {
        "name": "ctas_law_qa", "display_name": "党纪法规问答",
        "description": "基于本地纪检法规库检索作答，逐条给出法规名称与条号，无依据不作答。",
        "prompt": COMMON + "你是党纪法规问答助手：先用知识库检索相关法规，再用 get_regulation_article 按法规名称与条号取原文核对；"
                  "回答逐条注明《法规名称》第X条并引用原文要点；检索不到依据时明确回答「本地法规库未检索到依据」，不得凭记忆作答。"
                  "检索纪律：①在同一段代码里用 parallel_executor 并行完成检索与取条，工具调用总共不超过 3 轮；"
                  "②get_regulation_article 返回 found=false 或「尚未收录」即确认库内没有，不换说法重复检索；"
                  "③工具返回 JSON 字符串，先 json.loads 再取字段；④只回答所问，不主动扩展到问题未涉及的罪名、程序或数额标准。",
        "tools": ["kb", "mcp:get_regulation_article"], "temperature": 0.1, "max_steps": 8,
        "greeting": "请输入党纪法规问题，例如：党员违规收受礼金如何处分？",
    },
    {
        "name": "ctas_analyst", "display_name": "纪检研判助手",
        "description": "结合平台业务数据、确定性引擎结果与法规库，做线索研判、类案比对、审理协审和文书审查。",
        "prompt": COMMON + "你是纪检研判助手：区分「系统数据显示」「规则引擎结论」与「你的推断」；需要案件数据或量纪说理、证据链、程序合规结果时调用平台工具；"
                  "需要法规依据时检索知识库并用 get_regulation_article 核对原文；输出要点式意见并单列「待核实事项」。"
                  "调用纪律：先在同一段代码里用 parallel_executor 并行取齐所需的案件、信访、审理上下文与引擎结果，再集中核对法规，"
                  "工具调用总共不超过 5 轮；法规库返回 found=false 或「尚未收录」即停止该方向检索；工具返回 JSON 字符串时先 json.loads；"
                  "只核对与所问事项直接相关的法条。",
        "tools": ["kb", "mcp"], "temperature": 0.2, "max_steps": 12,
        "greeting": "请描述要研判的线索、案件或文书。",
    },
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--kb", default="纪检监察业务平台法规库")
    ap.add_argument("--mcp", default="ctas_tools", help="业务平台 MCP 服务名（未注册时跳过）")
    a = ap.parse_args()

    with get_db_session() as db:
        u = db.scalars(select(UserTenant).where(func.lower(UserTenant.user_email) == a.email.lower(),
                                                UserTenant.delete_flag == "N")).one()
        uid, tid, email = u.user_id, u.tenant_id, u.user_email
        kb_tool = db.scalars(select(ToolInfo).where(ToolInfo.author == tid, ToolInfo.class_name == "KnowledgeBaseSearchTool",
                                                    ToolInfo.delete_flag == "N")).first()
        mcp_tools = db.scalars(select(ToolInfo).where(ToolInfo.author == tid, ToolInfo.usage == a.mcp,
                                                      ToolInfo.delete_flag == "N", ToolInfo.is_available.is_(True))).all()
        kb_tool_id = kb_tool.tool_id if kb_tool else None
        mcp_ids = {t.name: t.tool_id for t in mcp_tools}

    rec = get_knowledge_record(dict(knowledge_name=a.kb, tenant_id=tid))
    index = rec["index_name"] if rec else None
    model = tenant_config_manager.get_model_config("LLM_ID", tenant_id=tid)

    out = []
    for spec in AGENTS:
        agent = dict(
            name=spec["name"], display_name=spec["display_name"], description=spec["description"],
            author=email, prompt=spec["prompt"], business_description=spec["prompt"],
            model_ids=[model["model_id"]], enabled=True, is_main_agent=True, ingroup_permission="PRIVATE",
            max_steps=spec["max_steps"], greeting_message=spec["greeting"],
            model_params_override={str(model["model_id"]): {
                "temperature": spec["temperature"],
                **({"extra_params": spec["extra_params"]} if spec.get("extra_params") else {}),
            }},
        )
        aid = find_agent_id_by_agent_name(spec["name"], tid)
        if not aid:
            aid = create_agent(agent, tid, uid)["agent_id"]
        else:
            update_agent(aid, SimpleNamespace(**agent), uid)
        bound, keep = [], set()

        def bind(tool_id, params):
            create_or_update_tool_by_tool_info(SimpleNamespace(agent_id=aid, tool_id=tool_id, enabled=True, params=params), tid, uid)
            keep.add(tool_id)

        if "kb" in spec["tools"] and kb_tool_id and index:
            bind(kb_tool_id, dict(index_names=[index], top_k=6, search_mode="hybrid", rerank=False))
            bound.append("knowledge_base_search")
        for t in spec["tools"]:
            if t == "mcp":
                for tool_id in mcp_ids.values():
                    bind(tool_id, {})
                if mcp_ids:
                    bound.append(f"{a.mcp}×{len(mcp_ids)}")
            elif t.startswith("mcp:") and t[4:] in mcp_ids:
                bind(mcp_ids[t[4:]], {})
                bound.append(t[4:])
        # 停用清单之外的旧绑定（如早期版本绑定的 get_rule），保证智能体工具集与清单一致
        with get_db_session() as db:
            stale = db.scalars(select(ToolInstance).where(ToolInstance.agent_id == aid, ToolInstance.tenant_id == tid,
                                                          ToolInstance.version_no == 0, ToolInstance.enabled.is_(True),
                                                          ToolInstance.tool_id.notin_(keep or {-1}))).all()
            stale_ids = [r.tool_id for r in stale]
        for tool_id in stale_ids:
            create_or_update_tool_by_tool_info(SimpleNamespace(agent_id=aid, tool_id=tool_id, enabled=False, params=None), tid, uid)
        ver = publish_version_impl(aid, tid, uid, f"{spec['display_name']}（业务平台场景智能体）", "业务平台场景智能体安装 / 更新")
        out.append(dict(name=spec["name"], agent_id=aid, tools=bound, version=ver.get("version_no")))
    print(json.dumps(dict(tenant=tid[:8], kb_index=index, model=model.get("model_name") or model.get("display_name"),
                          agents=out), ensure_ascii=False))


if __name__ == "__main__":
    main()
