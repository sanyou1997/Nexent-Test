#!/bin/sh
# 在容器内构建前端（宿主机无需安装 Node.js）：源码只读挂载，依赖装在独立卷 ctas-fe-node-modules，
# 产物写回 frontend/dist 并同步到 backend/web（镜像构建时打包）。
# 用法：sh scripts/build_frontend.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
case "$(uname -s)" in MINGW*|MSYS*) SRC="$(cd "$ROOT/frontend" && pwd -W)";; *) SRC="$ROOT/frontend";; esac
export MSYS_NO_PATHCONV=1
docker run --rm -v "$SRC:/src" -v ctas-fe-node-modules:/w/node_modules -w /w node:20-alpine sh -c '
  set -e
  cp /src/package.json /src/package-lock.json /src/tsconfig.json /src/vite.config.ts /src/index.html /w/
  rm -rf /w/src && cp -r /src/src /w/src
  [ -d /src/public ] && rm -rf /w/public && cp -r /src/public /w/public || true
  npm install --no-audit --no-fund --loglevel=error
  node_modules/.bin/tsc -b
  node_modules/.bin/vite build
  rm -rf /src/dist && cp -r /w/dist /src/dist
'
rm -rf "$ROOT/backend/web" && cp -r "$ROOT/frontend/dist" "$ROOT/backend/web"
echo "前端已构建：$(ls "$ROOT/backend/web/assets" | tr '\n' ' ')"
