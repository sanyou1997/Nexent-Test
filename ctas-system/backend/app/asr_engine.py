"""ASR 引擎（进程隔离封装）：供 asr.py 与 transcripts.py 共用。

将音频字节写入临时文件，调用 asr_worker.py 子进程完成转写，解析其 JSON 结果。
子进程崩溃（非零退出 / 无输出 / 超时）统一抛 ASREngineError，由调用方转成 HTTP 错误，
因此原生 crash 永远不会拖垮 uvicorn 主服务。
"""
import os
import sys
import json
import tempfile
import subprocess

from app.config import settings

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asr_worker.py")
_PY = sys.executable
_TIMEOUT = int(os.getenv("ASR_TIMEOUT", "300"))


def to_simplified(text: str) -> str:
    """繁 -> 简（opencc t2s）。供调用方/测试复用，惰性 import 不触发 whisper 加载。"""
    if not text:
        return text
    try:
        from opencc import OpenCC
    except Exception:
        return text
    try:
        return OpenCC("t2s").convert(text)
    except Exception:
        return text


class ASREngineError(RuntimeError):
    pass


def transcribe_audio(data: bytes, *, filename: str | None = None,
                     segments: bool = False, vad: bool = False,
                     language: str = "zh", speakers: int = 0,
                     timeout: int | None = None) -> dict:
    """返回 {"text","duration_s","language","diarization","speakers",["segments"]}；
    失败抛 ASREngineError。speakers>=2 时 segments 带 speaker 索引（启发式分离）。"""
    if not data:
        raise ASREngineError("音频为空")
    # 识别服务按「系统管理 → 识别服务」配置：Nexent 的 Paraformer / Qwen3-ASR，或本机 faster-whisper
    from app import recognizer
    cfg = recognizer.effective()
    provider = cfg["asr"]["provider"]
    if provider == "paraformer" and language not in ("zh", "", None):
        provider = cfg["asr"]["provider"] = "qwen_asr"  # Paraformer 仅普通话；其它语种交 Qwen3-ASR
    if provider != "builtin":
        try:
            return recognizer.remote_transcribe(data, filename or "audio.webm", segments=segments,
                                                language=language, speakers=speakers,
                                                timeout=timeout or _TIMEOUT, cfg=cfg)
        except Exception as e:
            if not (cfg["asr"]["fallback_builtin"] and recognizer.builtin_available()["asr"]):
                raise ASREngineError(f"语音识别服务不可用（{recognizer.asr_label(cfg)}）：{e}")
    ext = os.path.splitext(filename or "audio.webm")[1] or ".webm"
    fd, tmppath = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        cmd = [_PY, _WORKER, tmppath]
        if segments:
            cmd.append("--segments")
        if vad:
            cmd.append("--vad")
        if language and language != "zh":
            cmd.append(f"--lang={language}")
        if speakers and speakers >= 2:
            cmd.append(f"--speakers={speakers}")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout or _TIMEOUT)
        except subprocess.TimeoutExpired:
            raise ASREngineError(f"语音识别超时（>{(timeout or _TIMEOUT)}s）")
        if proc.returncode != 0 or not proc.stdout.strip():
            detail = (proc.stderr.strip() or proc.stdout.strip()
                      or "语音识别引擎异常退出")
            raise ASREngineError(detail)
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise ASREngineError(f"语音识别结果解析失败：{proc.stdout[:200]}")
        if "error" in result:
            raise ASREngineError(result["error"])
        return result
    finally:
        try:
            os.remove(tmppath)
        except OSError:
            pass
