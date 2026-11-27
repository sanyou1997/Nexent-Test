"""法规库 ↔ Nexent 知识库 联动状态。

平台法规库是主本，Nexent 知识库「纪检监察业务平台法规库」是检索副本，由 docker/nexent/sync_regulations_kb.sh
单向增量同步（入库必须走 Nexent 自身流水线，平台进程无权直接写 Nexent 知识库）。
本模块负责让联动「可见、可核对」：
- 同步脚本结束时调用 `python -m app.kb_sync record < 结果JSON`，记录本次同步进 Nexent 的各法规同步指纹；
- 平台随时按当前条文重算指纹，与记录比对，得出每部法规：已同步 / 有更新待同步 / 未同步，以及知识库中待移除的旧文件。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from app.config import BASE_DIR

STATE_FILE = Path(os.getenv("CTAS_KB_SYNC_FILE", str(BASE_DIR / "data" / "kb_sync.json")))
SYNC_CMD = "sh docker/nexent/sync_regulations_kb.sh <租户管理员邮箱>"


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def record(result: dict, manifest: dict) -> dict:
    """result：sync_regulations_kb.py 输出；manifest：本次导出的 {文件名: {fingerprint, title}}。
    仍在入库处理中的文件不记为已同步。"""
    pending = set(result.get("still_processing") or [])
    files = {f: m["fingerprint"] for f, m in manifest.items() if f not in pending}
    state = {
        "kb": result.get("kb", ""), "index": result.get("index", ""), "tenant": result.get("tenant", ""),
        "synced_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "files": files, "still_processing": sorted(pending),
        "last_run": {k: result.get(k) for k in ("uploaded", "skipped", "removed")},
    }
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return state


def status(db) -> dict:
    from app.kb_export import build
    state = load_state()
    synced = state.get("files") or {}
    items, counts = [], {"synced": 0, "changed": 0, "new": 0}
    current = build(db)
    for fname, f in current.items():
        if fname not in synced:
            st = "new"
        elif synced[fname] != f["fingerprint"]:
            st = "changed"
        else:
            st = "synced"
        counts[st] += 1
        items.append({"regulation_id": f["regulation_id"], "title": f["title"], "file": fname,
                      "articles": f["articles"], "status": st})
    removed = sorted(set(synced) - set(current))
    return {
        "configured": bool(state), "kb": state.get("kb") or "纪检监察业务平台法规库", "index": state.get("index", ""),
        "synced_at": state.get("synced_at", ""), "still_processing": state.get("still_processing") or [],
        "total": len(current), **counts, "removed": removed,
        "in_sync": bool(state) and counts["changed"] == 0 and counts["new"] == 0 and not removed,
        "items": items, "sync_command": SYNC_CMD,
    }


if __name__ == "__main__":
    # 用法：python -m app.kb_sync record [manifest.json] < 同步结果JSON
    if len(sys.argv) >= 2 and sys.argv[1] == "record":
        mpath = Path(sys.argv[2] if len(sys.argv) > 2 else "/tmp/ctas_kb/manifest.json")
        res = json.loads(sys.stdin.read().strip().splitlines()[-1])
        st = record(res, json.loads(mpath.read_text(encoding="utf-8")))
        print(json.dumps({"recorded": len(st["files"]), "synced_at": st["synced_at"]}, ensure_ascii=False))
    else:
        raise SystemExit("用法：python -m app.kb_sync record [manifest.json] < 同步结果JSON")
