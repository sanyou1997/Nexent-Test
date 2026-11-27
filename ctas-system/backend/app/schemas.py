from pydantic import BaseModel
from typing import Optional

class LoginIn(BaseModel):
    username: str
    password: str

class UserOut(BaseModel):
    id: int
    username: str
    real_name: str
    role: str
    class Config:
        from_attributes = True

class HandoffIn(BaseModel):
    """跨部门移交：承办部门（不填则自动确定；有多个可承办部门时须指定）。"""
    dept_id: int | None = None


class UserCreate(BaseModel):
    username: str
    password: str
    real_name: str = ""
    role: str = "banan"
    dept_id: int | None = None

class AskIn(BaseModel):
    question: str

class Citation(BaseModel):
    article_id: int
    regulation: str
    chapter: str
    article_no: str
    content: str
    score: float

class AskOut(BaseModel):
    question: str
    answer: str
    mode: str            # llm / retrieval / refused
    citations: list[Citation] = []

class PetitionCreate(BaseModel):
    content: str
    petitioner: str = "匿名"
    channel: str = "网络"
    petitioner_type: str = ""   # 实名 / 匿名（空则按 petitioner 推断）
    contact: str = ""           # 实名举报人联系方式（受理告知/办结反馈用）

class PetitionUpdateIn(BaseModel):
    """信访件人工修订（摘要/分类）。所有字段可选。"""
    summary: Optional[str] = None
    biz_type: Optional[str] = None
    labels: Optional[list[str]] = None

class DocumentGenerateIn(BaseModel):
    template_id: int
    case_name: str = ""
    fields: dict[str, str]

class DocumentUpdateIn(BaseModel):
    content: str
    note: str = ""

class TranscriptReviseIn(BaseModel):
    """语音转录人工校对：保存校对后的全文。"""
    text: str = ""

class VerifyIn(BaseModel):
    content: str
    doc_type: str = ""

class AssistantChatIn(BaseModel):
    message: str
    history: list[dict] = []
    mode: str = "general"   # general / draft

class AssistantSource(BaseModel):
    article_id: int
    regulation: str
    chapter: str
    article_no: str
    content: str
    score: float

class AssistantOut(BaseModel):
    answer: str
    mode: str
    sources: list[AssistantSource] = []
    llm_used: bool = False

class EducationGenerateIn(BaseModel):
    topic: str
    audience: str = "党员干部"
    count: int = 5

class EducationQuiz(BaseModel):
    question: str
    options: list[str] = []   # 空数组表示开放式思考题
    answer: str = ""
    explain: str = ""

class EducationOut(BaseModel):
    topic: str
    audience: str
    material: str
    quizzes: list[EducationQuiz] = []
    sources: list[AssistantSource] = []
    llm_used: bool = False

class ArchiveIn(BaseModel):
    title: Optional[str] = None
    source: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    secret_level: Optional[str] = None
    retention: Optional[str] = None

class ArchiveOut(BaseModel):
    id: int
    archive_no: str
    title: str
    source: str
    category: str
    ocr_text: str
    page_count: int
    char_count: int
    status: str
    file_name: str
    file_type: str
    has_file: bool = False
    uploaded_by_name: str = ""
    created_at: str = ""
    secret_level: str = "内部"
    retention: str = "30年"
    has_boxes: bool = False
    duplicate_of: Optional[int] = None


class HistoricalReportBatchDeleteIn(BaseModel):
    """批量删除请求：必须带当前登录账号与密码（二次确认，防误删/防越权）。"""
    ids: list[int] = []
    username: str = ""
    password: str = ""


class HistoricalReportSearchIn(BaseModel):
    """类案参考检索请求体。"""
    q: str = ""
    top_k: int = 8
    report_type: str = ""
    year: int = 0
    region: str = ""
    problem_category: str = ""


class HistoricalReportReferenceIn(BaseModel):
    """类案引用留痕：引用某份历史报告（可精确到问题条目）时记录审计链。"""
    report_id: int
    finding_id: int = 0
    query: str = ""
    note: str = ""


class HistoricalReportIn(BaseModel):
    """历史报告人工补录 / 修正（仅需传要改的字段）。"""
    title: Optional[str] = None
    report_no: Optional[str] = None
    report_type: Optional[str] = None
    report_date: Optional[str] = None
    region: Optional[str] = None
    audited_org: Optional[str] = None
    audit_type: Optional[str] = None
    problem_category: Optional[str] = None
    severity: Optional[str] = None
    amount: Optional[float] = None
    subject: Optional[str] = None
    findings: Optional[str] = None
    conclusion: Optional[str] = None
    tags: Optional[list] = None
    status: Optional[str] = None


class HistoricalReportOut(BaseModel):
    id: int
    report_no: str = ""
    title: str = ""
    report_type: str = ""
    report_date: str = ""
    year: int = 0
    region: str = ""
    audited_org: str = ""
    audit_type: str = ""
    problem_category: str = ""
    severity: str = ""
    amount: float = 0.0
    subject: str = ""
    findings: str = ""
    conclusion: str = ""
    legal_refs: list = []
    tags: list = []
    char_count: int = 0
    page_count: int = 0
    file_name: str = ""
    file_type: str = ""
    has_file: bool = False
    extract_confidence: float = 0.0
    missing_fields: list = []
    status: str = "已入库"
    near_duplicate_of: int = 0
    source: str = ""
    uploaded_by_name: str = ""
    created_at: str = ""
