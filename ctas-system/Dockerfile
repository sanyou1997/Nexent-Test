# 纪检监察业务辅助平台 · Docker 镜像
# 内容：后端（FastAPI，app.main，全业务单应用）+ 已构建的前端（backend/web）+ 法规语料与演示夹具（app/fixtures）。
# 前端需先在宿主机构建并复制到 backend/web（见「Docker部署说明.md」），镜像构建不再联网拉 npm 依赖。
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KMP_DUPLICATE_LIB_OK=TRUE \
    TZ=Asia/Shanghai \
    CTAS_WEB_DIR=web \
    CTAS_APP_NAME=纪检监察业务辅助平台 \
    HF_HUB_OFFLINE=1

# 可选：内网 / 国内镜像源，例如 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /app

COPY backend/requirements-docker.txt /app/requirements-docker.txt
RUN pip install -i "$PIP_INDEX_URL" -r /app/requirements-docker.txt \
 # nlp_typo 只读取 pycorrector 自带的混淆集/词频数据文件，不 import 其模型代码，故不装其依赖（torch 等）
 && pip install -i "$PIP_INDEX_URL" --no-deps "pycorrector==1.1.4" \
 # 只允许 headless 版 OpenCV（带 GUI 的 opencv-python 依赖 libxcb/libGL，slim 镜像没有）
 && (pip uninstall -y opencv-python 2>/dev/null || true) \
 && python -c "import cv2, pypdfium2, psycopg; print('cv2', cv2.__version__)"

COPY backend/app /app/app
COPY backend/web /app/web
COPY backend/data/corpus /app/data/corpus
COPY backend/ctas_selfcheck.py backend/platform_check.py backend/tenancy_check.py /app/
COPY docker/entrypoint.sh /app/entrypoint.sh
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8090/api/health',timeout=4)" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
