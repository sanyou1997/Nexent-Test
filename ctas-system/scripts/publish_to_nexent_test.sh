#!/bin/sh
# 把本仓库已提交的代码（HEAD）同步到 Nexent-Test 仓库的 ctas-system/ 子目录并提交，不推送。
# - 只同步已提交的文件（git archive），不带本仓库的提交历史与作者信息：Nexent-Test 是公开仓库，
#   提交统一用该仓库的同步身份（noreply 邮箱）。
# - 两边都必须没有未提交的改动；先对 Nexent-Test 做 git pull --ff-only。
# 用法：sh scripts/publish_to_nexent_test.sh [git commit 的额外参数，如 --trailer "..."]
#   Nexent-Test 路径默认 ../guide-work/Nexent-Test，可用环境变量 NEXENT_TEST_DIR 指定。
# 同步后检查：git -C <Nexent-Test> show --stat HEAD；推送：git -C <Nexent-Test> push origin main
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$(cd "${NEXENT_TEST_DIR:-$ROOT/../guide-work/Nexent-Test}" && pwd)"
SYNC_NAME="Nexent configuration sync"
SYNC_EMAIL="nexent-sync@users.noreply.github.com"

fail() { echo "$1"; exit 1; }
# 目标必须是 Nexent-Test 克隆的根目录（下面会整体替换其中的 ctas-system/）
git -C "$DEST" rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "$DEST 不是 git 仓库"
[ -z "$(git -C "$DEST" rev-parse --show-cdup)" ] || fail "$DEST 不是仓库根目录"
git -C "$DEST" remote get-url origin | grep -q "Nexent-Test" || fail "$DEST 的 origin 不是 Nexent-Test"
[ "$DEST/ctas-system" != "$ROOT" ] || fail "目标与本仓库相同"
[ -z "$(git -C "$ROOT" status --porcelain)" ] || fail "ctas-system 有未提交的改动，先提交再同步"
[ -z "$(git -C "$DEST" status --porcelain)" ] || fail "$DEST 有未提交的改动"
git -C "$DEST" pull --ff-only --quiet

REV="$(git -C "$ROOT" rev-parse --short HEAD)"
# 上次同步到的版本（写在提交说明的 Synced-From 行里），用于列出本次包含的提交
PREV="$(git -C "$DEST" log -1 --format=%B -- ctas-system | sed -n 's/^Synced-From: ctas-system \([0-9a-f]*\).*/\1/p')"

rm -rf "$DEST/ctas-system"
mkdir -p "$DEST/ctas-system"
git -C "$ROOT" archive HEAD | tar -x -C "$DEST/ctas-system"
git -C "$DEST" add -A ctas-system
if git -C "$DEST" diff --cached --quiet; then echo "ctas-system 没有变化"; exit 0; fi

if [ -n "$PREV" ] && git -C "$ROOT" cat-file -e "$PREV^{commit}" 2>/dev/null; then
  SUBJECT="Update ctas-system to $REV"
  BODY="$(git -C "$ROOT" log --reverse --format='- %s' "$PREV..HEAD")"
else
  SUBJECT="Add ctas-system at $REV"
  BODY="$(git -C "$ROOT" log --reverse --format='- %s' HEAD)"
fi
git -C "$DEST" -c user.name="$SYNC_NAME" -c user.email="$SYNC_EMAIL" \
  commit --quiet -m "$SUBJECT" -m "$BODY" -m "Synced-From: ctas-system $REV" "$@"

N="$(git -C "$ROOT" ls-files | wc -l | tr -d ' ')"
M="$(git -C "$DEST" ls-files ctas-system | wc -l | tr -d ' ')"
[ "$N" = "$M" ] || { echo "警告：文件数不一致（本仓库 $N，Nexent-Test/ctas-system $M），检查 Nexent-Test 的 .gitignore"; exit 1; }
git -C "$DEST" log -1 --stat --format='%h %an <%ae>%n%s' | tail -n 3
echo "已提交到 $DEST（$M 个文件），检查后推送：git -C \"$DEST\" push origin main"
