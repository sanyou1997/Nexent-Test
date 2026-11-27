#!/bin/sh
# 一键部署 / 升级纪检监察业务辅助平台，可重复执行（已完成的步骤自动跳过）。
# 用法：sh scripts/deploy.sh [Nexent 账号邮箱] [--nexent] [--check] [--keep-memory]
#   Nexent 账号邮箱  放业务智能体的 Nexent 账号；首次必须给出，脚本记入 .env（CTAS_NEXENT_EMAIL），以后可省略。
#                    不需要该账号的密码：接入步骤在 nexent-config 容器内按邮箱调用 Nexent 自身的服务函数。
#   --nexent         先在 Nexent-Test 根目录运行 restore.py start（Nexent 覆盖文件有更新时用）
#   --check          最后运行 scripts/run_checks.sh（全模块功能检查）
#   --keep-memory    不关闭该账号的智能体记忆（默认关闭，避免智能体把其他案件的内容带进回答）
# 步骤：1 环境检查  2 .env（首次自动生成随机密钥）  3 备份数据库（升级时）  4 构建并启动
#       5 接入 Nexent：API Key、智能体记忆、识别服务只读账号、法规知识库、MCP、场景智能体  6 状态
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export MSYS_NO_PATHCONV=1   # Windows Git Bash 下不改写容器内路径；Linux 下无影响。调用的子脚本自行处理，调用时清除

EMAIL=""; CHECK=0; MEMORY=off; NEXENT=0; WARN=0
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;;
    --keep-memory) MEMORY=keep ;;
    --nexent) NEXENT=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    -*) echo "未知参数：$a（sh scripts/deploy.sh --help 查看用法）"; exit 2 ;;
    *) EMAIL="$a" ;;
  esac
done

step() { printf '\n== %s\n' "$1"; }
die() { printf '\n[失败] %s\n' "$1" >&2; exit 1; }
warn() { printf '[注意] %s\n' "$1"; WARN=$((WARN + 1)); }
envget() { sed -n "s/^$1=//p" .env 2>/dev/null | tail -n 1 | tr -d '\r'; }
envset() {
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else printf '%s=%s\n' "$1" "$2" >> .env; fi
}
rand() { od -An -N24 -tx1 /dev/urandom | tr -d ' \n'; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
wait_healthy() {
  i=0; s=""
  while [ $i -lt 90 ]; do
    s="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null || true)"
    [ "$s" = healthy ] && return 0
    [ "$s" = exited ] && break
    sleep 2; i=$((i + 1))
  done
  docker logs --tail 40 "$1" 2>&1 || true
  die "$1 未就绪（状态：${s:-不存在}），见上方日志"
}
nexent_status() {
  docker exec -w /app ctas python -c "from app import nexent_client as n; s=n.status(force=True); print('ok' if s['authorized'] else (s.get('error') or '未接入'))" 2>/dev/null || echo "无法查询"
}
NX() { docker exec -w /opt/backend -e PYTHONPATH=/opt/backend "$@"; }

# ---------------------------------------------------------------- 0
if [ $NEXENT = 1 ]; then
  step "0 更新 Nexent（restore.py start）"
  [ -f "$ROOT/../restore.py" ] || die "--nexent 需要在 Nexent-Test 仓库里运行（找不到 ../restore.py）"
  PY="$(command -v python3 || command -v python || true)"
  [ -n "$PY" ] || die "需要 Python 3 运行 restore.py"
  (cd "$ROOT/.." && "$PY" -X utf8 restore.py start)
fi

# ---------------------------------------------------------------- 1
step "1/6 环境检查"
docker version >/dev/null 2>&1 || die "无法访问 Docker：检查 docker 服务是否启动、当前用户是否有权限（可加 sudo）"
docker compose version >/dev/null 2>&1 || die "需要 Docker Compose v2（docker compose 命令）"
for c in nexent-config nexent-northbound nexent-postgresql; do
  running "$c" || die "Nexent 容器 $c 未运行：先部署 / 启动 Nexent（Nexent-Test 根目录 restore.py start，或本脚本加 --nexent）"
done
docker network inspect nexent_network >/dev/null 2>&1 || die "缺少 Docker 网络 nexent_network（Nexent 未用 Docker 部署？）"
if ! docker network inspect nexent_local_ocr >/dev/null 2>&1; then
  docker network create nexent_local_ocr >/dev/null && echo "已创建网络 nexent_local_ocr"
fi
echo "Docker、Nexent 容器与网络正常"

# ---------------------------------------------------------------- 2
step "2/6 配置 .env"
VOL=0; docker volume inspect ctas_ctas-pgdata >/dev/null 2>&1 && VOL=1
if [ ! -f .env ]; then
  [ $VOL = 1 ] && die "已有业务数据库卷 ctas_ctas-pgdata，但缺少 .env：请放回原来的 .env（CTAS_DB_PASSWORD 必须与初始化时一致）"
  cp .env.docker.example .env
  envset CTAS_BIND 0.0.0.0
  echo "已由 .env.docker.example 生成 .env（CTAS_BIND=0.0.0.0：允许其他机器访问 8090 端口）"
else
  echo "沿用已有 .env"
fi
chmod 600 .env 2>/dev/null || true
for k in CTAS_SECRET_KEY CTAS_DB_PASSWORD CTAS_MCP_TOKEN; do
  case "$(envget $k)" in
    ""|*请改为*)
      [ $k = CTAS_DB_PASSWORD ] && [ $VOL = 1 ] && die ".env 中 CTAS_DB_PASSWORD 未设置，但数据库卷已存在：请填回初始化时的密码"
      envset $k "$(rand)"; echo "已生成随机 $k" ;;
  esac
done
[ -n "$EMAIL" ] && envset CTAS_NEXENT_EMAIL "$EMAIL"
EMAIL="${EMAIL:-$(envget CTAS_NEXENT_EMAIL)}"

# ---------------------------------------------------------------- 3
step "3/6 备份业务数据库"
if running ctas-db; then
  mkdir -p backups
  f="ctas-$(date +%Y%m%d-%H%M%S).dump"
  docker exec ctas-db pg_dump -U ctas -Fc -f "/tmp/$f" ctas
  docker cp "ctas-db:/tmp/$f" "backups/$f" >/dev/null
  docker exec ctas-db rm -f "/tmp/$f"
  ls -1t backups/ctas-*.dump 2>/dev/null | tail -n +11 | while read -r old; do rm -f "$old"; done
  echo "已备份到 backups/$f（保留最近 10 份；恢复方法见 Docker部署说明.md 第六节）"
else
  echo "首次部署，无需备份"
fi

# ---------------------------------------------------------------- 4
step "4/6 构建并启动（首次构建需下载 Python 依赖，约几分钟）"
docker compose build \
  || die "镜像构建失败（见上方输出）。下载 Python 依赖慢或失败时，在 .env 加 CTAS_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple 后重跑"
if ! docker compose up -d; then
  # 替换容器中途失败时，新建的容器可能停在 Created：先把平台拉起来，避免停服
  docker compose up -d --no-recreate ctas-db ctas >/dev/null 2>&1 || true
  die "启动失败（见上方输出），已尝试恢复平台容器。若提示旧容器无法停止（is zombie / cannot be killed），重启 Docker 后重跑本脚本"
fi
wait_healthy ctas
wait_healthy ctas-mcp
echo "ctas、ctas-db、ctas-mcp 已就绪"

# ---------------------------------------------------------------- 5
step "5/6 接入 Nexent"
if [ -z "$EMAIL" ]; then
  warn "未给出 Nexent 账号邮箱，跳过接入。补做：sh scripts/deploy.sh <Nexent 账号邮箱>"
else
  echo "Nexent 账号：$EMAIL"
  docker cp docker/nexent/account_setup.py nexent-config:/tmp/account_setup.py >/dev/null

  # 5a API Key：平台已能调用 Nexent 时不动；否则取该账号已有的 Key（没有才新建），经管道直接写入平台，不在屏幕显示
  if [ "$(nexent_status)" = ok ]; then
    echo "[API Key] 平台已接入 Nexent，保持不变"
  else
    NX nexent-config python /tmp/account_setup.py --email "$EMAIL" --emit-key \
      | docker exec -i -w /app ctas python -c "import sys; from app import nexent_client as n; k=sys.stdin.read().strip(); assert k, '未取得 API Key'; n.save_settings({'api_key': k, 'enabled': True})" \
      || die "API Key 配置失败（见上方提示）"
    s="$(nexent_status)"
    [ "$s" = ok ] || die "API Key 已写入平台，但调用 Nexent 失败：$s"
    echo "[API Key] 已写入平台并验证通过"
  fi

  # 5b 智能体记忆
  if [ $MEMORY = off ]; then
    NX nexent-config python /tmp/account_setup.py --email "$EMAIL" --memory off || warn "关闭智能体记忆失败，可在 Nexent 的记忆设置中手动关闭"
  fi

  # 5c 识别服务跟随 Nexent「文件解析」配置：建只读数据库账号（只能读识别配置表）
  if [ -n "$(envget NEXENT_DB_DSN)" ]; then
    echo "[识别服务] 已跟随 Nexent「文件解析」配置"
  else
    PGU="$(docker exec nexent-postgresql sh -c 'echo $POSTGRES_USER')"
    PGD="$(docker exec nexent-postgresql sh -c 'echo $POSTGRES_DB')"
    has="$(docker exec nexent-postgresql psql -U "$PGU" -d "$PGD" -tAc "select to_regclass('nexent.document_parser_settings') is not null" 2>/dev/null || true)"
    if [ "$has" = t ]; then
      pw="$(rand)"
      docker exec -i nexent-postgresql psql -q -v ON_ERROR_STOP=1 -U "$PGU" -d "$PGD" -v pw="$pw" < docker/nexent-readonly-role.sql >/dev/null \
        || die "创建识别服务只读账号失败（见上方输出）"
      envset NEXENT_DB_DSN "postgresql://ctas_reader:$pw@nexent-postgresql:5432/$PGD"
      docker compose up -d
      wait_healthy ctas
      echo "[识别服务] 已设为跟随 Nexent「文件解析」配置"
    else
      warn "该 Nexent 没有「文件解析」设置，识别服务按平台「系统管理 → 识别服务」的配置运行"
    fi
  fi

  # 5d 法规库 → Nexent 知识库（增量）
  echo "[法规知识库] 同步中（首次约几分钟）..."
  (unset MSYS_NO_PATHCONV; sh docker/nexent/sync_regulations_kb.sh "$EMAIL") \
    || warn "法规知识库同步失败（见上方输出）。若提示没有向量模型，在 Nexent「模型配置」为该账号添加向量模型后重跑本脚本"

  # 5e 业务平台 MCP（令牌经环境变量传入，不出现在进程参数里）
  docker cp docker/nexent/register_ctas_mcp.py nexent-config:/tmp/register_ctas_mcp.py >/dev/null
  if (CTAS_MCP_TOKEN="$(envget CTAS_MCP_TOKEN)"; export CTAS_MCP_TOKEN
      NX -e CTAS_MCP_TOKEN nexent-config python /tmp/register_ctas_mcp.py --email "$EMAIL"); then
    echo "[MCP] ctas_tools 已注册"
  else
    warn "MCP 注册失败，见上方输出"
  fi

  # 5f 场景智能体
  docker cp docker/nexent/install_scene_agents.py nexent-config:/tmp/install_scene_agents.py >/dev/null
  if NX nexent-config python /tmp/install_scene_agents.py --email "$EMAIL"; then
    echo "[场景智能体] 已安装 / 更新"
  else
    warn "场景智能体安装失败：多为该账号还没配置默认对话模型，见上方输出"
  fi
fi

# ---------------------------------------------------------------- 6
step "6/6 状态"
docker compose ps
echo "Nexent 接入：$(nexent_status)"
if [ $CHECK = 1 ]; then
  (unset MSYS_NO_PATHCONV; sh scripts/run_checks.sh) || warn "功能检查有未通过项，见上方输出"
fi
PORT="$(envget CTAS_PORT)"; PORT="${PORT:-8090}"
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
case "$(envget CTAS_BIND)" in 0.0.0.0) HOST="${IP:-<服务器IP>}" ;; *) HOST=127.0.0.1 ;; esac
printf '\n完成：http://%s:%s' "$HOST" "$PORT"
[ $WARN -gt 0 ] && printf '（%s 项需要注意，见上方 [注意]）' "$WARN"
printf '\n首次部署：用 admin 登录后立即修改密码，在「用户管理」重置或停用演示账号，在「部门与权限」按实际机构调整。\n'
