FROM codex-local-asr:1.0
COPY wheels/ /tmp/qwen-wheels/
RUN python -m pip install --no-cache-dir --no-index --find-links=/tmp/qwen-wheels sherpa-onnx==2.0.3 && rm -rf /tmp/qwen-wheels
COPY sherpa-onnx-qwen3-asr-0.6B-int8-2026-03-25/ /opt/asr/models/qwen/
COPY vad/ /opt/asr/models/vad/
COPY service.py /opt/asr/service.py
WORKDIR /opt/asr
CMD ["python","-m","uvicorn","service:app","--host","0.0.0.0","--port","8000"]
