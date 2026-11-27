from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app import audit
from app.database import get_db
from app.deps import get_current_user
from app.llm import provider
from app.models import User
from app.schemas import AssistantChatIn, AssistantOut

router = APIRouter(prefix="/api/assistant", tags=["A2 纪检办公通用助手"])

# 通用问答：直接走本机大模型对话，不做法规检索、不强制附法规依据。
SYS_GENERAL = (
    "你是纪检监察机关的办公智能助手，部署在机关内网本机运行。任务是像一位得力的办公同事一样，"
    "陪用户自然对话、帮忙处理日常文字工作：撰写与润色通知、简报、会议纪要、谈话提纲、汇报材料；"
    "梳理思路、归纳要点、提炼标题、改写法言法语；解释常见办公与业务流程；对不确定的事实主动说明而非编造。"
    "语气自然、条理清晰、说人话，不堆砌术语。若用户问到党纪法规的具体条款或定性量纪，提醒其以正式条文"
    "和审批程序为准，不要替用户下结论。使用简体中文。"
)
SYS_DRAFT = (
    "你是纪检监察机关文书起草助手。根据用户描述的需求，生成规范、可直接使用的文书草稿"
    "（Markdown 格式），结构完整、用语准确，对需要人工填实的字段用【】标注，并在末尾列出"
    "使用提示。不编造事实性结论，仅生成可供人工完善的框架与规范表述。"
)


_DRAFT_TEMPLATES = {
    "谈话": ("谈话提纲", [
        "# 谈话提纲",
        "",
        "## 一、谈话对象",
        "- 姓名：【】　单位及职务：【】　政治面貌：【】",
        "",
        "## 二、谈话时间地点",
        "- 时间：【】　地点：【】　谈话人：【】　记录人：【】",
        "",
        "## 三、谈话目的",
        "围绕【反映的问题概述】开展核实性谈话，固定相关事实。",
        "",
        "## 四、谈话要点（建议顺序）",
        "1. 核实身份与告知权利义务；",
        "2. 就被反映问题逐一询问，注意让其本人陈述；",
        "3. 对矛盾点追问并固定；",
        "4. 告知如实说明义务及法律责任；",
        "5. 核对笔录、签名捺印。",
        "",
        "## 五、需准备的证明材料",
        "- 【书证/物证清单】",
        "",
        "> 使用提示：谈话须二人以上在场，全程录音录像，笔录经对象核对无误后签名。",
    ]),
    "通知": ("工作通知", [
        "# 关于【事项】的工作通知",
        "",
        "各【相关单位/部门】：",
        "",
        "根据【依据/要求】，现就【事项】通知如下：",
        "",
        "## 一、工作内容",
        "【具体任务描述】",
        "",
        "## 二、责任分工",
        "牵头单位：【】　配合单位：【】",
        "",
        "## 三、完成时限",
        "请于【日期】前报送【材料/结果】。",
        "",
        "## 四、工作要求",
        "1. 高度重视，明确专人负责；",
        "2. 确保材料真实、及时；",
        "3. 严守工作纪律与保密要求。",
        "",
        "联系人：【】　电话：【】",
        "",
        "【发文单位】",
        "【日期】",
    ]),
    "纪要": ("会议纪要", [
        "# 【会议名称】会议纪要",
        "",
        "时间：【】　地点：【】　主持人：【】　记录人：【】",
        "参会：【】　缺席：【】",
        "",
        "## 一、会议议题",
        "【议题概述】",
        "",
        "## 二、主要情况",
        "【讨论与汇报要点】",
        "",
        "## 三、议定事项",
        "1. 【事项一及责任单位/时限】",
        "2. 【事项二】",
        "",
        "## 四、下一步要求",
        "【落实要求】",
        "",
        "> 使用提示：纪要须如实记录议定事项，经主持人审签后印发。",
    ]),
    "初核": ("初步核实方案", [
        "# 【问题线索】初步核实方案",
        "",
        "## 一、线索来源与反映问题",
        "来源：【】　反映主要问题：【】",
        "",
        "## 二、核实目的",
        "查清反映问题是否属实，提出处置建议。",
        "",
        "## 三、核实方式与步骤",
        "1. 调阅【书证材料】；",
        "2. 谈话核实【相关人员】；",
        "3. 比对【数据/账目】；",
        "4. 形成初步核实情况报告。",
        "",
        "## 四、人员与时限",
        "核查组：【】　时限：【】",
        "",
        "## 五、纪律与保密",
        "依规依纪依法开展，注意安全与保密。",
    ]),
}


def _offline_draft(message: str) -> str:
    key = next((k for k in _DRAFT_TEMPLATES if k in message), None)
    if key:
        title, lines = _DRAFT_TEMPLATES[key]
        return "\n".join(lines) + (
            f"\n\n（离线说明：未联网调用大模型，已根据关键词生成《{title}》标准模板，"
            "请人工填实【】字段后使用。）"
        )
    return (
        "# 文书草稿（通用）\n\n"
        "## 一、标题\n【文书名称】\n\n"
        "## 二、正文\n【按以下结构组织：背景/依据 → 主要事实或事项 → 处理意见或要求 → 落款】\n\n"
        "## 三、落款\n【发文单位】\n【日期】\n\n"
        "（离线说明：未联网调用大模型，已生成通用草稿框架，请据实填实【】字段。）"
    )


@router.post("/chat", response_model=AssistantOut)
def chat(data: AssistantChatIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    msg = (data.message or "").strip()
    if not msg:
        return AssistantOut(answer="请输入您的问题或起草需求。", mode=data.mode, sources=[], llm_used=False)

    mode = data.mode if data.mode in ("general", "draft") else "general"
    sources: list = []
    llm_used = False
    answer = ""

    if mode == "draft":
        gen = provider.chat(
            [{"role": "system", "content": SYS_DRAFT},
             {"role": "user", "content": f"请起草以下文书：{msg}"}],
            scene="assistant", user_id=user.id,
        )
        if gen:
            answer = gen
            llm_used = True
        else:
            answer = _offline_draft(msg)
    else:  # general：直接走本机大模型对话，不做法规检索
        history_msgs = data.history[-8:] if data.history else []
        messages = (
            [{"role": "system", "content": SYS_GENERAL}]
            + history_msgs
            + [{"role": "user", "content": msg}]
        )
        gen = provider.chat(messages, scene="assistant", user_id=user.id)
        if gen:
            answer = gen
            llm_used = True
        else:
            answer = ("本机大模型暂未就绪，通用助手需要本地大模型（Ollama / qwen2.5:3b）支持对话。"
                      "请确认本机 Ollama 服务已启动并加载对应模型后重试。")

    audit.write_log(db, user.id, user.username, action="assistant_chat",
                    detail={"mode": mode, "message": msg[:200],
                            "sources": [], "llm_used": llm_used})
    return AssistantOut(answer=answer, mode=mode, sources=sources, llm_used=llm_used)


@router.get("/status")
def status(user: User = Depends(get_current_user)):
    return {"llm_available": provider.llm_available()}
