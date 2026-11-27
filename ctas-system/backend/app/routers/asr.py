"""ASR 语音识别：进程隔离（防崩溃拖垮服务）。

- 通过 app.asr_engine 调用 asr_worker.py 子进程：模型加载/转写在独立进程完成；
- 子进程崩溃（native crash / 超时 / 非零退出）只影响本次请求，主服务不受影响；
- 未安装依赖时返回 501。
"""
import time

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.audit import write_log
from app.asr_engine import transcribe_audio, ASREngineError

router = APIRouter(prefix="/api/asr", tags=["ASR"])


@router.post("")
async def asr(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    from app import recognizer
    if recognizer.effective()["asr"]["provider"] == "builtin" and not recognizer.builtin_available()["asr"]:
        raise HTTPException(501, "语音识别引擎未安装，请在「系统管理 → 识别服务」使用 Nexent 的转写服务")

    data = await file.read()
    if not data:
        raise HTTPException(400, "上传文件为空")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "音频超过 25MB 限制")

    t0 = time.time()
    try:
        result = transcribe_audio(data, filename=file.filename)
    except ASREngineError as e:
        raise HTTPException(422, f"音频解析失败：{e}")

    text = result.get("text", "")
    duration = result.get("duration_s", 0)

    write_log(db, user_id=user.id, username=user.username, action="asr",
              resource_type="audio", resource_id=file.filename or "",
              detail={"chars": len(text), "duration_s": duration,
                      "latency_ms": int((time.time() - t0) * 1000)})

    return {"mode": "asr", "text": text, "duration_s": duration}
