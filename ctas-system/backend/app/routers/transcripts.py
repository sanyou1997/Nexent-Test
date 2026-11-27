"""语音转录：长音频（谈话/会议录音）分段转录。

设计要点：
- 上传即入库（status=转录中），BackgroundTasks 后台转录，前端轮询进度
- 分段带时间戳（segments: [{start, end, text}]），支持人工校对后保存
- 复用 app.asr_engine 进程隔离转写（模型在子进程加载，崩溃不拖垮服务）
- 演示红线：转录在本机完成，音频不外传
"""
import io
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks, Form, Body
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.database import get_db
from app.deps import get_current_user
from app.audit import write_log
from app.models import Transcript, User
from app.asr_engine import transcribe_audio, ASREngineError
from app.schemas import TranscriptReviseIn

router = APIRouter(prefix="/api/transcripts", tags=["Transcription"])

MAX_AUDIO_MB = 100
AUDIO_DIR = BASE_DIR / "data" / "audio"


def _fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _default_speaker_names(n: int) -> dict:
    """默认说话人标签：说话人A/B/C…（n<2 时归为单一说话人A）。"""
    n = max(1, int(n or 1))
    return {i: f"说话人{chr(65 + i)}" for i in range(n)}


def _transcribe_bg(tid: int, data: bytes, lang: str = "zh", speaker_count: int = 0):
    """后台任务：独立会话转录并回写结果（含语种/说话人分离）。"""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        t = db.get(Transcript, tid)
        if not t:
            return
        try:
            # 注：vad_filter=True（Silero VAD，走 onnxruntime）在本环境（Windows+py3.13）
            # 原生崩溃(segfault)，故关闭 VAD；说话人分离改用 whisper 段时间戳做停顿切分，
            # 不依赖 VAD，稳定不崩溃。
            result = transcribe_audio(data, filename=t.filename, segments=True, vad=False,
                                      language=lang, speakers=speaker_count)
            segs = []
            for s in (result.get("segments") or []):
                segs.append({"start": s.get("start", 0), "end": s.get("end", 0),
                             "text": s.get("text", ""), "ts": _fmt_ts(s.get("start", 0)),
                             "speaker": s.get("speaker", 0)})
            t.segments = segs
            t.text = result.get("text", "")
            t.duration_s = result.get("duration_s", 0)
            t.language = result.get("language", "zh")
            t.lang = lang
            eff_spk = max(1, result.get("speakers", speaker_count or 1))
            t.speaker_count = eff_spk
            t.speaker_names = _default_speaker_names(eff_spk)
            t.status = "完成"
        except ASREngineError as e:
            t.status = "失败"
            t.error = str(e)[:280]
        db.commit()
    finally:
        db.close()


@router.post("/upload")
async def upload(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    lang: str = Form("zh"),
    speaker_count: int = Form(0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from app import recognizer
    if recognizer.effective()["asr"]["provider"] == "builtin" and not recognizer.builtin_available()["asr"]:
        raise HTTPException(501, "语音识别引擎未安装，请在「系统管理 → 识别服务」使用 Nexent 的转写服务")

    data = await file.read()
    if not data:
        raise HTTPException(400, "上传文件为空")
    if len(data) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(413, f"音频超过 {MAX_AUDIO_MB}MB 限制")

    # 音频字节落盘暂存，避免后台任务持有内存副本（demo 规模直接内存传递亦可）
    spk = max(0, int(speaker_count or 0))
    t = Transcript(filename=file.filename or "audio", status="转录中",
                   created_by=user.id, lang=lang,
                   speaker_count=max(1, spk) if spk >= 2 else 0,
                   speaker_names=_default_speaker_names(max(1, spk)) if spk >= 2 else {})
    db.add(t)
    db.commit()
    db.refresh(t)

    # 音频落盘（本机保存，用于音文对照回放；不外传）
    name = file.filename or "audio"
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "audio"
    ext = "".join(c for c in ext if c.isalnum())[:8] or "audio"
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / f"{t.id}.{ext}"
    path.write_bytes(data)
    t.audio_path = str(path)
    db.commit()

    write_log(db, user_id=user.id, username=user.username, action="transcribe_upload",
              resource_type="transcript", resource_id=t.id,
              detail={"filename": t.filename, "size_mb": round(len(data) / 1048576, 1)})

    background.add_task(_transcribe_bg, t.id, data, lang, spk)
    return {"id": t.id, "status": t.status, "filename": t.filename}


def _out(t: Transcript, creator: str = "") -> dict:
    return {
        "id": t.id, "filename": t.filename, "duration_s": t.duration_s,
        "language": t.language, "status": t.status, "error": t.error,
        "segments": t.segments or [], "text": t.text,
        "lang": t.lang or "zh", "speaker_count": t.speaker_count or 0,
        "speaker_names": t.speaker_names or {},
        "minutes": t.minutes or "", "minutes_status": t.minutes_status or "",
        "minutes_model": t.minutes_model or "", "minutes_error": t.minutes_error or "",
        "has_audio": bool(t.audio_path) and Path(t.audio_path).is_file(),
        "created_by": creator,
        "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
    }


@router.get("")
def list_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Transcript).order_by(Transcript.id.desc()).all()
    if not rows:
        return []
    # 批量预取创建者，避免 N+1
    uids = {t.created_by for t in rows if t.created_by}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    return [_out(t, (users.get(t.created_by).real_name if t.created_by and users.get(t.created_by) else ""))
            for t in rows]


@router.get("/{tid}")
def get_one(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(Transcript, tid)
    if not t:
        raise HTTPException(404, "转录记录不存在")
    c = db.get(User, t.created_by) if t.created_by else None
    return _out(t, c.real_name if c else "")


@router.get("/{tid}/audio")
def get_audio(tid: int, db: Session = Depends(get_db),
              user: User = Depends(get_current_user)):
    """音文对照：回放本机保存的原始音频（支持断点续传/拖动定位）。"""
    t = db.get(Transcript, tid)
    if not t or not t.audio_path:
        raise HTTPException(404, "该记录未保存音频文件")
    p = Path(t.audio_path)
    if not p.is_file():
        raise HTTPException(404, "音频文件已不存在")
    return FileResponse(p, media_type=_audio_mime(p.suffix))


def _audio_mime(ext: str) -> str:
    return {
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
        ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
        ".mp4": "video/mp4", ".webm": "audio/webm",
    }.get(ext.lower(), "application/octet-stream")


def _minutes_bg(tid: int, text: str, lang: str, user_id: int):
    """后台任务：调用纪要生成，回写结果。"""
    from app.database import SessionLocal
    from app.minutes import generate_minutes
    db = SessionLocal()
    try:
        t = db.get(Transcript, tid)
        if not t:
            return
        t.minutes_status = "生成中"
        db.commit()
        res = generate_minutes(text, lang=lang, user_id=user_id)
        if res["ok"]:
            t.minutes = res["minutes"]
            t.minutes_json = res["json"]
            t.minutes_status = "完成"
            t.minutes_model = res["model"]
        else:
            t.minutes_status = "失败"
            t.minutes_error = (res.get("error") or "")[:280]
        db.commit()
    finally:
        db.close()


@router.post("/{tid}/minutes")
def gen_minutes(tid: int, background: BackgroundTasks,
                db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """后台生成会议纪要（LLM 调用较慢，避免阻塞请求）。"""
    t = db.get(Transcript, tid)
    if not t:
        raise HTTPException(404, "转录记录不存在")
    if t.status != "完成":
        raise HTTPException(400, "请等待转录完成后再生成纪要")
    if not t.text or not t.text.strip():
        raise HTTPException(400, "转录文本为空，无法生成纪要")
    t.minutes_status = "生成中"
    t.minutes_error = ""
    db.commit()
    background.add_task(_minutes_bg, tid, t.text, t.lang or "zh", user.id)
    write_log(db, user_id=user.id, username=user.username, action="transcript_minutes",
              resource_type="transcript", resource_id=tid,
              detail={"chars": len(t.text)})
    return {"ok": True, "status": "生成中"}


@router.get("/{tid}/minutes")
def get_minutes(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(Transcript, tid)
    if not t:
        raise HTTPException(404, "转录记录不存在")
    return {"minutes": t.minutes or "", "minutes_json": t.minutes_json or {},
            "minutes_status": t.minutes_status or "", "minutes_model": t.minutes_model or "",
            "minutes_error": t.minutes_error or ""}


@router.put("/{tid}/minutes")
def edit_minutes(tid: int, body: dict = Body(...), db: Session = Depends(get_db),
                 user: User = Depends(get_current_user)):
    """人工编辑/补正纪要（保存留痕）。"""
    t = db.get(Transcript, tid)
    if not t:
        raise HTTPException(404, "转录记录不存在")
    t.minutes = body.get("minutes", t.minutes) or ""
    if "minutes_json" in body:
        t.minutes_json = body["minutes_json"]
    t.minutes_status = "完成"
    db.commit()
    write_log(db, user_id=user.id, username=user.username, action="transcript_minutes_edit",
              resource_type="transcript", resource_id=tid)
    return {"ok": True}


@router.put("/{tid}/speakers")
def rename_speakers(tid: int, body: dict = Body(...), db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    """重命名说话人标签（如 谈话人 / 被谈话人 / 主持人），mapping {index: name}。"""
    t = db.get(Transcript, tid)
    if not t:
        raise HTTPException(404, "转录记录不存在")
    names = body.get("speaker_names")
    if not isinstance(names, dict):
        raise HTTPException(422, "speaker_names 须为对象 {index: name}")
    t.speaker_names = {int(k): str(v) for k, v in names.items()}
    t.speaker_count = max(t.speaker_count or 1, len(t.speaker_names) or 1)
    db.commit()
    write_log(db, user_id=user.id, username=user.username, action="transcript_speakers",
              resource_type="transcript", resource_id=tid)
    return {"ok": True, "speaker_names": t.speaker_names}


@router.put("/{tid}")
def revise(tid: int, body: TranscriptReviseIn, db: Session = Depends(get_db),
           user: User = Depends(get_current_user)):
    """人工校对：保存全文（原文分段保留，校对文本另存）。"""
    t = db.get(Transcript, tid)
    if not t:
        raise HTTPException(404, "转录记录不存在")
    t.text = body.text
    db.commit()
    write_log(db, user_id=user.id, username=user.username, action="transcribe_revise",
              resource_type="transcript", resource_id=tid,
              detail={"chars": len(t.text)})
    c = db.get(User, t.created_by) if t.created_by else None
    return _out(t, c.real_name if c else "")


@router.delete("/{tid}")
def remove(tid: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = db.get(Transcript, tid)
    if not t:
        raise HTTPException(404, "转录记录不存在")
    db.delete(t)
    db.commit()
    write_log(db, user_id=user.id, username=user.username, action="transcribe_delete",
              resource_type="transcript", resource_id=tid)
    return {"ok": True}
