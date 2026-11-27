#!/bin/sh
# 业务平台法规库 → Nexent 知识库 单向同步（增量：按同步指纹只重传有变化的法规）。
# 用法：sh docker/nexent/sync_regulations_kb.sh <租户管理员邮箱> [知识库名称]
# 同步后如首次创建知识库，再执行 install_scene_agents.py --kb <知识库名称> 把它绑定给场景智能体。
set -e
EMAIL="${1:?用法：sync_regulations_kb.sh <租户管理员邮箱> [知识库名称]}"
KB="${2:-纪检监察业务平台法规库}"
HERE="$(cd "$(dirname "$0")" && pwd)"
# Windows Git Bash 下避免把容器内路径（/tmp、/app）改写成 Windows 路径；Linux 下无影响
X="env MSYS_NO_PATHCONV=1"

echo "[1/3] 从业务平台导出法规库 ..."
$X docker exec -w /app ctas sh -c 'rm -rf /tmp/ctas_kb && python -m app.kb_export /tmp/ctas_kb'

echo "[2/3] 拷入 Nexent（容器间直接传输，不落宿主机）..."
$X docker exec nexent-config rm -rf /tmp/ctas_kb
$X docker exec ctas tar -C /tmp -cf - ctas_kb | $X docker exec -i nexent-config tar -C /tmp -xf -
docker cp "$HERE/sync_regulations_kb.py" nexent-config:/tmp/sync_regulations_kb.py

echo "[3/4] 入库（Nexent 标准流水线：MinIO + data-process，按标题切块）..."
RESULT="$($X docker exec -w /opt/backend -e PYTHONPATH=/opt/backend nexent-config \
  python /tmp/sync_regulations_kb.py --email "$EMAIL" --kb "$KB" --dir /tmp/ctas_kb 2>/dev/null | tail -1)"
echo "$RESULT"

echo "[4/4] 回写同步记录到业务平台（法规库页面据此显示各法规的联动状态）..."
printf '%s\n' "$RESULT" | $X docker exec -i -w /app ctas python -m app.kb_sync record /tmp/ctas_kb/manifest.json
