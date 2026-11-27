#!/bin/sh
# 全模块功能检查 + 审理引擎自检：在一次性克隆库上运行，不触碰业务库。
#   1) ctas-db 内把业务库 ctas 克隆为 ctas_check（pg_dump | psql，业务库在线也可克隆）
#   2) 把 testdata/ 拷入 ctas 容器，分别运行 platform_check.py（全模块）、ctas_selfcheck.py（审理引擎 107 项）
#      与 tenancy_check.py（部门隔离：模块把关 / 数据隔离 / 移交授权 / 数据范围 / MCP 部门令牌）
#   3) 删除克隆库；结果 JSON 拷回 scripts/platform_check.json
# 用法：sh scripts/run_checks.sh [platform|selfcheck|tenancy|all]（默认 all）
# 说明：检查会真实调用 OCR / ASR / Nexent 场景智能体（产生少量 Nexent 会话与调用记录），耗时约 5–10 分钟。
set -e
WHAT="${1:-all}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
X="env MSYS_NO_PATHCONV=1"   # Windows Git Bash：不改写容器内路径

echo "[1/4] 克隆业务库 → ctas_check ..."
$X docker exec ctas-db sh -c 'dropdb -U ctas --if-exists ctas_check && createdb -U ctas ctas_check && pg_dump -U ctas ctas | psql -q -U ctas -d ctas_check >/dev/null'
trap '$X docker exec ctas-db dropdb -U ctas --if-exists ctas_check >/dev/null 2>&1 || true' EXIT

echo "[2/4] 拷入测试样本 ..."
$X docker exec ctas rm -rf /tmp/testdata
(cd "$ROOT" && tar -cf - testdata) | $X docker exec -i ctas tar -C /tmp -xf -
docker cp "$ROOT/backend/platform_check.py" ctas:/app/platform_check.py
docker cp "$ROOT/backend/ctas_selfcheck.py" ctas:/app/ctas_selfcheck.py
docker cp "$ROOT/backend/tenancy_check.py" ctas:/app/tenancy_check.py

RC=0
if [ "$WHAT" = all ] || [ "$WHAT" = platform ]; then
  echo "[3/4] 全模块功能检查 ..."
  $X docker exec -w /app -e CTAS_TESTDATA=/tmp/testdata ctas sh -c 'DATABASE_URL="${DATABASE_URL%/ctas}/ctas_check" python platform_check.py' || RC=1
  docker cp ctas:/tmp/platform_check.json "$ROOT/scripts/platform_check.json" 2>/dev/null || true
fi
if [ "$WHAT" = all ] || [ "$WHAT" = selfcheck ]; then
  echo "[4/4] 审理引擎自检 ..."
  $X docker exec -w /app ctas sh -c 'DATABASE_URL="${DATABASE_URL%/ctas}/ctas_check" python ctas_selfcheck.py' | tail -12 || RC=1
fi
if [ "$WHAT" = all ] || [ "$WHAT" = tenancy ]; then
  echo "[+] 部门隔离自检 ..."
  OUT=$($X docker exec -w /app ctas sh -c 'DATABASE_URL="${DATABASE_URL%/ctas}/ctas_check" python tenancy_check.py' 2>&1) || RC=1
  echo "$OUT" | grep -E "^\[FAIL\]|自检|Error" || true
fi
exit $RC
