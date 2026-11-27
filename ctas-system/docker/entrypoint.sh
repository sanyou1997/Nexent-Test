#!/bin/sh
# 纪检监察业务辅助平台：容器启动。
# 数据库在 PostgreSQL（DATABASE_URL）；表结构由 ORM 建立，演示数据由 app/demo_data.py 幂等加载。
set -e
mkdir -p /app/data
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8090 --proxy-headers --forwarded-allow-ips="*"
