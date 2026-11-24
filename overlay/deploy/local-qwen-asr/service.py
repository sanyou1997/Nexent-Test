# Copyright (c) 2025-2026 Huawei Technologies Co., Ltd. All rights reserved.
"""Offline Qwen3-ASR transcription endpoint for self-hosted Nexent deployments."""
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

from fastapi import FastAPI, Form, HTTPException, UploadFile

MODEL = "Qwen/Qwen3-ASR-0.6B"
ROOT = Path("/opt/asr")
WEIGHTS = ROOT / "models/qwen"
SR = 16000
UPLOAD_LIMIT = 256 * 1024 * 1024
MAX_SECONDS = 7200
slot = Lock()
app = FastAPI()


def recognize(source, dest):
    import sherpa_onnx
    import soundfile as sf
    from funasr_onnx import Fsmn_vad
    # These helpers from the Paraformer service disable network access in this worker.
    from app.transcribe import merge_spans, split_span

    wav = dest.with_suffix(".wav")
    subprocess.run([str(ROOT / "bin/ffmpeg"), "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file,pipe",
                    "-i", str(source), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SR),
                    "-t", str(MAX_SECONDS + 1), "-c:a", "pcm_s16le", str(wav)], check=True, timeout=600)
    segments = []
    with sf.SoundFile(wav) as audio:
        duration = len(audio) / SR
        if duration > MAX_SECONDS:
            raise ValueError("Recording exceeds two hours")
        vad = Fsmn_vad(str(ROOT / "models/vad"), quantize=True, device_id="-1", intra_op_num_threads=2)
        spans = []
        for offset in range(0, len(audio), 60 * SR):
            audio.seek(offset)
            block = audio.read(62 * SR, dtype="float32")
            if len(block) < 400:
                continue
            detected = vad(block)
            if not isinstance(detected, list):
                raise ValueError("Invalid VAD result")
            for start, end in detected[0] if detected else ():
                spans.append((max(0, offset + round(start * SR / 1000)), min(len(audio), offset + round(end * SR / 1000))))
        if spans:
            recognizer = sherpa_onnx.OfflineRecognizer.from_qwen3_asr(
                conv_frontend=str(WEIGHTS / "conv_frontend.onnx"), encoder=str(WEIGHTS / "encoder.int8.onnx"),
                decoder=str(WEIGHTS / "decoder.int8.onnx"), tokenizer=str(WEIGHTS / "tokenizer"),
                num_threads=4, provider="cpu", max_total_len=1024, max_new_tokens=256)
            for start, end in merge_spans(spans):
                for a, b in split_span(audio, start, end, 20 * SR):
                    audio.seek(a)
                    stream = recognizer.create_stream()
                    stream.accept_waveform(SR, audio.read(b - a, dtype="float32"))
                    recognizer.decode_stream(stream)
                    text = stream.result.text.strip()
                    if text:
                        segments.append({"start": a / SR, "end": b / SR, "text": text})
    dest.write_text(json.dumps({"text":"\n".join(x["text"] for x in segments), "segments":segments,
                                "duration":duration}, ensure_ascii=False), encoding="utf8")


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model"}]}


@app.post("/v1/audio/transcriptions")
def transcribe(file: UploadFile, model: str = Form(...), response_format: str = Form("json")):
    if model != MODEL or response_format not in {"json", "verbose_json"}:
        raise HTTPException(400, "Unsupported model or response format")
    if not slot.acquire(blocking=False):
        raise HTTPException(429, "Transcription in progress")
    try:
        with TemporaryDirectory() as work:
            source, dest = Path(work) / "input", Path(work) / "result.json"
            with source.open("wb") as output:
                while chunk := file.file.read(1024 * 1024):
                    if output.tell() + len(chunk) > UPLOAD_LIMIT:
                        raise HTTPException(413, "Recording exceeds file limit")
                    output.write(chunk)
            subprocess.run([sys.executable, __file__, str(source), str(dest)], check=True, timeout=7200)
            return json.loads(dest.read_text("utf8"))
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Transcription timed out") from None
    except subprocess.CalledProcessError:
        raise HTTPException(422, "Unable to transcribe this recording") from None
    finally:
        slot.release()


if __name__ == "__main__":
    recognize(Path(sys.argv[1]), Path(sys.argv[2]))
