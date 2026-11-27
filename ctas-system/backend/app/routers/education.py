from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app import audit
from app.database import get_db
from app.deps import get_current_user
from app.llm import provider
from app.models import User
from app.rag import retrieve
from app.schemas import EducationGenerateIn, EducationOut, EducationQuiz, AssistantSource

router = APIRouter(prefix="/api/education", tags=["A3 廉洁教育智能体"])

SYS = (
    "你是纪检监察机关廉洁教育智能体，负责根据党纪法规生成面向指定对象的廉洁教育学习材料与随堂测验。"
    "学习材料使用 Markdown，必须以「# 廉洁教育学习材料：<主题>」为标题，"
    "并严格包含以下四个一级标题（措辞不得改动）："
    "「一、学习背景」「二、法规要点」「三、案例警示」「四、学习小结」。"
    "法规要点须标注条款号与原文；小结须回扣学习要求。"
    "随堂测验为若干道单项选择题，每题含 4 个选项、正确选项文本与解析。"
    "仅依据所提供条款生成，不得编造条款；返回严格 JSON："
    '{"material": "字符串", "quizzes": [{"question": "字符串", "options": ["A","B","C","D"], "answer": "正确选项文本", "explain": "字符串"}]}。'
)


def _src(h: dict) -> AssistantSource:
    return AssistantSource(
        article_id=h["article_id"], regulation=h["regulation"], chapter=h["chapter"],
        article_no=h["article_no"], content=h["content"], score=h["score"],
    )


def _offline_education(topic: str, audience: str, hits: list[dict], count: int) -> EducationOut:
    if not hits:
        material = (
            f"# 廉洁教育学习材料：{topic}\n\n"
            f"## 一、学习对象\n{audience}\n\n"
            f"## 二、说明\n知识库中未检索到与「{topic}」直接相关的法规条款，"
            "本材料暂以通用廉洁要求提示：严守政治纪律、组织纪律、廉洁纪律、群众纪律、工作纪律、生活纪律，"
            "自觉接受监督。具体条款请以最新法规文本为准。\n\n"
            "（离线说明：未联网调用大模型，未检索到专门依据，故未生成具体测验，避免误导。）"
        )
        return EducationOut(topic=topic, audience=audience, material=material, quizzes=[], sources=[], llm_used=False)

    lines = [f"# 廉洁教育学习材料：{topic}", "", f"## 一、学习对象\n{audience}", "",
             "## 二、法规要点", ""]
    for h in hits:
        lines.append(f"- 《{h['regulation']}》{h['chapter']} {h['article_no']}：{h['content']}")
    lines += [
        "",
        "## 三、学习要求",
        "1. 准确理解上述条款的适用情形与纪律要求；",
        "2. 结合岗位职责开展对照检视，防微杜渐；",
        "3. 将纪律规矩内化于心、外化于行。",
        "",
        "（离线说明：未联网调用大模型，材料由本机检索引擎从法规库匹配生成，全部要点附原文出处。）",
    ]
    material = "\n".join(lines)

    quizzes: list[EducationQuiz] = []
    for h in hits[:max(1, min(count, len(hits)))]:
        quizzes.append(EducationQuiz(
            question=f"请结合《{h['regulation']}》{h['article_no']}的规定，谈谈其对{audience}的警示意义。",
            options=[],
            answer="",
            explain=f"依据：《{h['regulation']}》{h['chapter']} {h['article_no']}：{h['content']}",
        ))
    return EducationOut(topic=topic, audience=audience, material=material, quizzes=quizzes,
                        sources=[_src(h) for h in hits], llm_used=False)


@router.post("/generate", response_model=EducationOut)
def generate(data: EducationGenerateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    topic = (data.topic or "").strip()
    if not topic:
        return EducationOut(topic="", audience=data.audience, material="请输入学习主题。", quizzes=[])

    result = retrieve.retrieve(db, topic)
    hits = result["hits"]

    if result["mode"] in ("refused", "empty"):
        out = _offline_education(topic, data.audience, hits, data.count)
    else:
        ctx = "\n\n".join(
            f"《{h['regulation']}》{h['chapter']} {h['article_no']}：{h['content']}" for h in hits
        )
        gen = provider.chat_json(
            [{"role": "system", "content": SYS},
             {"role": "user", "content": f"学习主题：{topic}\n面向对象：{data.audience}\n"
                                         f"测验题数：{data.count}\n\n可参考的法规依据：\n{ctx}"}],
            scene="education", user_id=user.id,
        )
        if gen and isinstance(gen, dict) and gen.get("material"):
            quizzes = [EducationQuiz(**q) for q in (gen.get("quizzes") or [])]
            out = EducationOut(topic=topic, audience=data.audience,
                               material=gen["material"], quizzes=quizzes,
                               sources=[_src(h) for h in hits], llm_used=True)
        else:
            out = _offline_education(topic, data.audience, hits, data.count)

    audit.write_log(db, user.id, user.username, action="education_generate",
                    detail={"topic": topic, "audience": data.audience,
                            "quizzes": len(out.quizzes), "llm_used": out.llm_used})
    return out
