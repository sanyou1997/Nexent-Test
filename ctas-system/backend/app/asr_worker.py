"""ASR 转写子进程（进程隔离）。

由 asr.py / transcripts.py 通过 subprocess 调用，模型加载与转写在独立子进程完成。
目的：faster-whisper 加载/推理是原生操作，在 PyInstaller 嵌入式 Python 下曾有
native crash；即便在普通 CPython 下偶发崩溃，也只影响本次请求，不会拖垮 uvicorn 主服务。

用法：
    python asr_worker.py <audio_path> [--segments] [--vad]
标准输出：成功 -> JSON {"text","duration_s","language","segments":[...]}
          失败 -> JSON {"error":"..."} 并以非 0 退出码结束（便于父进程判定崩溃）
"""
import io
import os
import sys
import json

# 允许以子进程方式 import app（cwd 一般为 backend）
HERE = os.path.dirname(os.path.abspath(__file__))          # backend/app
BACKEND = os.path.dirname(HERE)                            # backend
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from app.config import settings, BASE_DIR  # noqa: E402

MODEL_DIR = BASE_DIR / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)


def _patch_av_compat():
    """PyAV 15+ 移除了 open(metadata_errors=...) 参数，faster-whisper 仍在使用。
    打兼容补丁：忽略该参数重试，对旧版行为无影响。"""
    import av
    if getattr(av.open, "_jw_patched", False):
        return
    orig = av.open

    def patched_open(*args, **kwargs):
        try:
            return orig(*args, **kwargs)
        except TypeError:
            if "metadata_errors" in kwargs:
                kwargs.pop("metadata_errors")
                return orig(*args, **kwargs)
            raise

    patched_open._jw_patched = True
    av.open = patched_open


def _resolve_local_model(size: str):
    """离线优先：若预置了本地模型目录（backend/models/models--Systran--faster-whisper-<size>），
    直接返回其 snapshot 子目录路径，避免联网下载（政务内网无外网）。"""
    base = MODEL_DIR / f"models--Systran--faster-whisper-{size}"
    if base.exists():
        snap_dir = base / "snapshots"
        if snap_dir.is_dir():
            subs = [d for d in snap_dir.iterdir() if d.is_dir()]
            if subs:
                return str(subs[0])
        if (base / "model.bin").exists():
            return str(base)
    return None


# 繁 -> 简 单例（政务内网要求转写统一简体）。OpenMP 冲突修复后该模块已可安全加载。
_CC = None


def _to_simplified(text: str) -> str:
    """繁体字 -> 简体字（opencc t2s）。opencc 缺失或异常时原样返回，保证降级不崩。"""
    if not text:
        return text
    global _CC
    try:
        from opencc import OpenCC
    except Exception:
        return text
    try:
        if _CC is None:
            _CC = OpenCC("t2s")
        return _CC.convert(text)
    except Exception:
        return text


def _diarize_pause(segs, speakers, gap=2.5, max_turn=30.0):
    """停顿切分 + 启发式说话人标注（离线，无需 VAD/声纹模型）。

    - 相邻段间隔 >= gap 秒，或单轮累计 >= max_turn 秒 → 起新轮；
    - 轮流分配说话人索引 0..speakers-1（仅启发式，非声纹识别）。
    返回带 speaker 索引的段列表。speakers<2 时全部归为 0。
    """
    eff = max(1, int(speakers or 1))
    out = []
    turn_idx = -1
    turn_start = None
    prev_end = 0.0
    for s in segs:
        start = float(s.get("start", 0))
        end = float(s.get("end", start))
        new_turn = (
            turn_start is None
            or (start - prev_end) >= gap
            or (end - (turn_start or start)) >= max_turn
        )
        if new_turn:
            turn_idx += 1
            turn_start = start
        prev_end = end
        spk = turn_idx % eff if eff >= 2 else 0
        out.append({"start": round(start, 2), "end": round(end, 2),
                    "text": s.get("text", ""), "speaker": spk})
    return out


def main(argv):
    need_segments = "--segments" in argv
    use_vad = "--vad" in argv
    lang = "zh"
    speakers = 0
    for a in argv:
        if a.startswith("--lang="):
            lang = a.split("=", 1)[1] or "zh"
        elif a.startswith("--speakers="):
            try:
                speakers = int(a.split("=", 1)[1])
            except ValueError:
                speakers = 0
    pos = [a for a in argv if not a.startswith("--")]
    if len(pos) < 2:
        print(json.dumps({"error": "缺少音频路径参数"}))
        return 2
    path = pos[1]

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(json.dumps({"error": "语音识别引擎未安装（pip install faster-whisper）"}))
        return 3

    try:
        _patch_av_compat()
        local = _resolve_local_model(settings.ASR_MODEL)
        if local:
            model = WhisperModel(local, device="cpu", compute_type="int8")
        else:
            # 本地未预置时回退到联网下载
            model = WhisperModel(
                settings.ASR_MODEL, device="cpu", compute_type="int8",
                download_root=str(MODEL_DIR),
            )
        # language: "auto" 交由 whisper 自动检测；其余透传（zh/en/yue…）。
        # 注：标准 whisper 对粤语(yue)支持有限，需专粤语模型才准，界面已标注。
        lang_param = None if lang in ("auto", "", "detect") else lang
        segments, info = model.transcribe(
            path, language=lang_param, beam_size=5, vad_filter=use_vad,
        )
        raw_segs, parts = [], []
        for s in segments:
            sim_text = _to_simplified(s.text.strip())
            raw_segs.append({"start": s.start, "end": s.end, "text": sim_text})
            parts.append(sim_text)
        # 说话人分离：仅当请求 speakers>=2 时做停顿切分标注（离线、零新依赖）
        if need_segments and speakers >= 2:
            segs = _diarize_pause(raw_segs, speakers)
        elif need_segments:
            segs = [{"start": round(s["start"], 2), "end": round(s["end"], 2),
                     "text": s["text"], "speaker": 0} for s in raw_segs]
        else:
            segs = []
        out = {
            "text": "".join(parts),
            "duration_s": round(info.duration, 1),
            "language": (lang_param or info.language or "zh"),
            "diarization": "heuristic" if (need_segments and speakers >= 2) else "none",
            "speakers": max(1, speakers) if speakers >= 2 else 1,
        }
        if need_segments:
            out["segments"] = segs
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as e:  # 非崩溃类异常：返回错误 JSON，父进程可解析
        print(json.dumps({"error": f"音频解析失败：{e}"}))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
