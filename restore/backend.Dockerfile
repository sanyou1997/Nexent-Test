FROM python:3.11-slim AS pdf
RUN pip install --no-cache-dir --target=/opt/pdf 'pypdfium2>=4.30,<6' 'Pillow>=10'
FROM nexent/nexent:v2.8.5
COPY --from=pdf /opt/pdf/ /opt/backend/.venv/lib/python3.11/site-packages/
COPY overlay/backend/ /opt/backend/
COPY overlay/sdk/nexent/ /opt/backend/.venv/lib/python3.11/site-packages/nexent/
