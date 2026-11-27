"""监督模型算子管线引擎（零代码拖拽建模的执行内核）。

画布上的模型编译为 pipeline JSON：
  {"nodes": [{"id","type", ...params}], "edges": [{"from","to"}]}

六类算子：
  source    数据源（选已接入数据集，唯一起点）
  filter    过滤（字段 + 条件：eq/ne/gt/ge/lt/le/contains/not_null/empty）
  join      对碰关联（intersection 两库交集 / duplicate 同库重复 / inclusion 包含命中，
           第二输入由 dataset_b_id 指定）
  aggregate 聚合分组（group_by + sum/count/avg/max/min → agg_result 列）
  threshold 阈值判断（对 agg_result 或指定字段做 gt/ge/lt/le/eq 比较）
  output    输出预警（唯一终点，按 key_field 生成命中）

执行：拓扑排序（Kahn）后逐步 pandas 变换，逐步记录行数便于干跑预览；
单节点行数上限 MAX_ROWS 保护内存。纯规则、离线可用、不依赖大模型。
"""
from collections import deque

import pandas as pd
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Dataset
import json

MAX_ROWS = 200000  # 单节点行数上限（本地演示数据远低于此，防误操作拖垮内存）

NODE_TYPES = {
    "source": {"label": "数据源", "inputs": 0, "outputs": 1, "desc": "选择已接入的数据集（Excel/CSV）"},
    "filter": {"label": "过滤", "inputs": 1, "outputs": 1, "desc": "按字段条件筛选行"},
    "join": {"label": "对碰关联", "inputs": 1, "outputs": 1, "desc": "两库交集 / 同库重复 / 包含命中（第二数据源在参数里选）"},
    "aggregate": {"label": "聚合分组", "inputs": 1, "outputs": 1, "desc": "按关键字段分组聚合数值列"},
    "threshold": {"label": "阈值判断", "inputs": 1, "outputs": 1, "desc": "聚合值与常量比较，保留超阈值行"},
    "output": {"label": "输出预警", "inputs": 1, "outputs": 0, "desc": "生成预警（按关键字段逐条输出）"},
}
JOIN_RULES = ("intersection", "duplicate", "inclusion")
FILTER_OPS = ("eq", "ne", "gt", "ge", "lt", "le", "contains", "not_null", "empty")
TH_OPS = ("gt", "ge", "lt", "le", "eq")
AGG_FNS = ("sum", "count", "avg", "max", "min")


def _load_dataset(db: Session, did: int):
    ds = db.get(Dataset, did)
    if not ds:
        raise HTTPException(404, f"数据集 #{did} 不存在")
    rows = json.loads(ds.rows_json or "[]")
    return pd.DataFrame(rows), ds.name


def _num(s: pd.Series) -> pd.Series:
    """列转数值（容忍千分位/百分号），非数值变 NaN。"""
    if pd.api.types.is_numeric_dtype(s):
        return s
    return pd.to_numeric(
        s.astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False),
        errors="coerce",
    )


# ---------------- 拓扑校验 ----------------
def validate_pipeline(p: dict) -> list:
    """返回错误列表（空 = 通过）。前后端同规则，前端也有一份轻量实现。"""
    errs: list = []
    nodes = p.get("nodes") or []
    edges = p.get("edges") or []
    if not isinstance(nodes, list) or not nodes:
        return ["画布为空：至少需要 1 个数据源和 1 个输出节点"]
    by_id = {}
    for n in nodes:
        nid = n.get("id")
        if not nid:
            errs.append("存在没有 id 的节点")
            continue
        if nid in by_id:
            errs.append(f"节点 id 重复：{nid}")
        by_id[nid] = n
        if n.get("type") not in NODE_TYPES:
            errs.append(f"节点 {nid} 类型不支持：{n.get('type')}")
    ids = set(by_id)
    sources = [n for n in by_id.values() if n.get("type") == "source"]
    outputs = [n for n in by_id.values() if n.get("type") == "output"]
    if len(sources) < 1:
        errs.append(f"至少需要 1 个数据源节点（当前 {len(sources)} 个）")
    if len(outputs) != 1:
        errs.append(f"输出节点必须唯一（当前 {len(outputs)} 个）")
    # 边合法性
    indeg = {i: 0 for i in ids}
    outdeg = {i: 0 for i in ids}
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a not in ids or b not in ids:
            errs.append(f"连线引用了不存在的节点：{a} → {b}")
            continue
        outdeg[a] += 1
        indeg[b] += 1
    # 环检测 + 可达性（Kahn，用副本，避免污染后续孤立判断的 indeg）
    deg = dict(indeg)
    q = deque([i for i in ids if deg[i] == 0])
    seen = 0
    while q:
        cur = q.popleft(); seen += 1
        for e in edges:
            if e.get("from") == cur:
                deg[e["to"]] -= 1
                if deg[e["to"]] == 0:
                    q.append(e["to"])
    if seen != len(ids):
        errs.append("画布存在环，请重新连接")
    # 孤立/断头节点（source 无出边、output 无入边、中间节点缺入或出）
    for i in ids:
        t = by_id[i].get("type")
        if t == "source" and outdeg[i] == 0:
            errs.append(f"数据源「{by_id[i].get('name') or i}」还没有连接到下游")
        if t == "output" and indeg[i] == 0:
            errs.append("输出节点还没有接入上游")
        if t not in ("source", "output") and (indeg[i] == 0 or outdeg[i] == 0):
            errs.append(f"算子「{NODE_TYPES.get(t, {}).get('label', t)}」({i}) 连接不完整")
    # 算子参数校验
    for n in by_id.values():
        nid, t = n.get("id"), n.get("type")
        if t == "source" and not n.get("dataset_id"):
            errs.append(f"数据源 {nid} 未选择数据集")
        if t == "filter":
            if not n.get("field") or n.get("op") not in FILTER_OPS:
                errs.append(f"过滤节点 {nid}：字段或条件不完整")
        if t == "join":
            if n.get("rule") not in JOIN_RULES or not n.get("key_a"):
                errs.append(f"对碰节点 {nid}：规则或关键字段不完整")
            j_in = indeg.get(nid, 0)
            if j_in > 2:
                errs.append(f"对碰节点 {nid}：最多 2 个输入（当前 {j_in}）")
            elif j_in == 2:
                # 双输入模式：第二库来自上游节点
                if n.get("rule") == "intersection" and not n.get("key_b"):
                    errs.append(f"对碰节点 {nid}：交集模式需指定 B 侧关键字段 key_b")
                if n.get("rule") == "inclusion" and not n.get("field_b"):
                    errs.append(f"对碰节点 {nid}：包含命中需指定 B 侧字段 field_b")
            else:
                # 单输入（老模式）：第二库来自 dataset_b_id 参数
                if n.get("rule") in ("intersection", "inclusion") and not n.get("dataset_b_id"):
                    errs.append(f"对碰节点 {nid}：仅接 1 个上游时需指定第二数据集")
                if n.get("rule") == "inclusion" and not n.get("field_b"):
                    errs.append(f"对碰节点 {nid}：包含命中需指定 B 侧字段 field_b")
        if t == "aggregate":
            if not n.get("group_by"):
                errs.append(f"聚合节点 {nid}：未选择分组字段")
            if n.get("agg", "count") != "count" and not n.get("agg_field"):
                errs.append(f"聚合节点 {nid}：非 count 聚合需选择数值字段")
            if n.get("agg", "sum") not in AGG_FNS:
                errs.append(f"聚合节点 {nid}：不支持的聚合方式")
        if t == "threshold":
            if n.get("op") not in TH_OPS or n.get("value") in (None, ""):
                errs.append(f"阈值节点 {nid}：比较符或阈值不完整")
        if t == "output" and not n.get("key_field"):
            errs.append(f"输出节点 {nid}：未选择关键字段")
    return errs


def topo_order(p: dict) -> list:
    """Kahn 拓扑排序，返回节点 id 执行顺序（validate 通过后才调用）。"""
    nodes = {n["id"]: n for n in p.get("nodes") or []}
    edges = p.get("edges") or []
    indeg = {i: 0 for i in nodes}
    for e in edges:
        if e.get("to") in indeg:
            indeg[e["to"]] += 1
    q = deque(sorted([i for i in nodes if indeg[i] == 0]))
    order = []
    while q:
        cur = q.popleft(); order.append(cur)
        for e in edges:
            if e.get("from") == cur:
                indeg[e["to"]] -= 1
                if indeg[e["to"]] == 0:
                    q.append(e["to"])
    return order


# ---------------- 算子实现 ----------------
def _apply_filter(df: pd.DataFrame, n: dict) -> pd.DataFrame:
    field, op = n.get("field"), n.get("op")
    if field not in df.columns:
        raise HTTPException(422, f"过滤字段「{field}」不存在于当前数据")
    s = df[field]
    if op == "not_null":
        return df[s.notna() & (s.astype(str).str.strip() != "") & (s.astype(str).str.strip().str.lower() != "nan")]
    if op == "empty":
        return df[s.isna() | (s.astype(str).str.strip() == "") | (s.astype(str).str.strip().str.lower() == "nan")]
    if op == "contains":
        return df[s.astype(str).str.contains(str(n.get("value", "")), na=False)]
    sv = s.astype(str).str.strip()
    v = str(n.get("value", "")).strip()
    if op == "eq":
        return df[sv == v]
    if op == "ne":
        return df[sv != v]
    sn = _num(s)
    try:
        v = float(n.get("value"))
    except (TypeError, ValueError):
        raise HTTPException(422, f"数值比较需要数字阈值（当前：{n.get('value')}）")
    if op == "gt":
        return df[sn > v]
    if op == "ge":
        return df[sn >= v]
    if op == "lt":
        return df[sn < v]
    if op == "le":
        return df[sn <= v]
    raise HTTPException(422, f"不支持的过滤条件：{op}")


def _join_core(dfa: pd.DataFrame, dfb: pd.DataFrame, n: dict, names: dict,
               name_a: str, name_b: str) -> pd.DataFrame:
    """对碰核心：A 表(df) 与 B 表(df) 按规则对碰，返回命中行（含 _match_key）。"""
    rule, key_a = n.get("rule"), n.get("key_a")
    if key_a not in dfa.columns:
        raise HTTPException(422, f"关键字段「{key_a}」不存在于当前数据")
    ka = dfa[key_a].astype(str).str.strip()
    if rule == "duplicate":
        # 单库重复：只看 A 自身，B 不参与
        cnt = ka.map(ka.value_counts())
        out = dfa[cnt > 1].copy()
        out["_match_key"] = ka[cnt > 1]
        names["match_note"] = f"在「{name_a}」中「{key_a}」重复出现"
        return out
    if rule == "intersection":
        key_b = n.get("key_b") or key_a
        if key_b not in dfb.columns:
            raise HTTPException(422, f"第二数据集缺少关联字段「{key_b}」")
        set_b = {str(x).strip() for x in dfb[key_b].tolist() if str(x).strip()}
        mask = ka.isin(set_b)
        out = dfa[mask].copy()
        out["_match_key"] = ka[mask]
        names["match_note"] = f"对象同时出现在「{name_a}」与「{name_b}」（关键字段「{key_a}」对碰命中）"
        return out
    if rule == "inclusion":
        # A 的关键字段值出现在 B 的某字段文本中
        field_b = n.get("field_b")
        if not field_b or field_b not in dfb.columns:
            raise HTTPException(422, f"包含命中字段「{field_b}」不存在于第二数据集")
        blob = " ".join(str(x) for x in dfb[field_b].tolist())
        mask = ka.apply(lambda k: bool(k) and k in blob)
        out = dfa[mask].copy()
        out["_match_key"] = ka[mask]
        names["match_note"] = f"「{name_a}」中的对象出现在「{name_b}」字段「{field_b}」中"
        return out
    raise HTTPException(422, f"不支持的对碰规则：{rule}")


def _apply_join(db: Session, df: pd.DataFrame, n: dict, names: dict) -> pd.DataFrame:
    """老模式：第二库来自节点参数 dataset_b_id（单上游 join 的兜底兼容路径）。"""
    if not n.get("dataset_b_id"):
        raise HTTPException(422, "对碰节点未选择第二数据集")
    dfb, nb = _load_dataset(db, int(n["dataset_b_id"]))
    names["b"] = nb
    return _join_core(df, dfb, n, names, names.get("a", "数据集"), nb)


def _apply_join_df(dfa: pd.DataFrame, dfb: pd.DataFrame, n: dict, names: dict,
                   name_a: str, name_b: str) -> pd.DataFrame:
    """新模式：第二库来自上游节点（多数据流 DAG）。"""
    return _join_core(dfa, dfb, n, names, name_a, name_b)


def _apply_aggregate(df: pd.DataFrame, n: dict) -> pd.DataFrame:
    gb, agg = n.get("group_by"), n.get("agg", "count")
    if gb not in df.columns:
        raise HTTPException(422, f"分组字段「{gb}」不存在于当前数据")
    key = df[gb].astype(str).str.strip()
    tmp = pd.DataFrame({"_gk": key})
    if agg == "count":
        out = tmp.groupby("_gk").size().reset_index(name="agg_result")
    else:
        field = n.get("agg_field")
        if field not in df.columns:
            raise HTTPException(422, f"聚合字段「{field}」不存在于当前数据")
        tmp["_v"] = _num(df[field])
        g = tmp.groupby("_gk")["_v"]
        s = {"sum": g.sum, "avg": g.mean, "max": g.max, "min": g.min}[agg]()
        out = s.reset_index()
        out.columns = ["_gk", "agg_result"]
    out = out.rename(columns={"_gk": gb})
    return out


def _apply_threshold(df: pd.DataFrame, n: dict) -> pd.DataFrame:
    field = n.get("field") or "agg_result"
    if field not in df.columns:
        raise HTTPException(422, f"阈值字段「{field}」不存在（聚合节点是否未连接？）")
    sn = _num(df[field])
    try:
        v = float(n.get("value"))
    except (TypeError, ValueError):
        raise HTTPException(422, f"阈值需要数字（当前：{n.get('value')}）")
    op = n.get("op")
    m = {"gt": sn > v, "ge": sn >= v, "lt": sn < v, "le": sn <= v, "eq": sn == v}[op]
    return df[m].copy()


def _make_hits(df: pd.DataFrame, n: dict, names: dict) -> list:
    key_field = n.get("key_field") or "_match_key"
    if key_field not in df.columns:
        # 退化：用第一列作关键字段
        key_field = df.columns[0] if len(df.columns) else None
        if key_field is None:
            return []
    hits = []
    seen = set()
    for _, row in df.iterrows():
        k = str(row.get(key_field, "")).strip()
        if not k or k.lower() == "nan" or k in seen:
            continue
        seen.add(k)
        parts = []
        if names.get("match_note"):
            parts.append(names["match_note"])
        if "agg_result" in df.columns and pd.notna(row.get("agg_result")):
            try:
                parts.append(f"聚合值 {float(row['agg_result']):,.2f}")
            except (TypeError, ValueError):
                pass
        hits.append({"key": k, "reason": "；".join(parts) or "管线命中"})
    return hits


# ---------------- 主管线执行 ----------------
def run_pipeline(db: Session, pipeline: dict, collect_steps: bool = False) -> dict:
    """执行管线（多数据流 DAG）。返回 {hits, steps, hit_rows}；校验失败抛 422。

    每个节点的输出存于 df_map[node_id]，节点输入取各自上游（按 to_port 排序：0=A, 1=B）。
    允许多个数据源节点、多分支；对碰(join)节点可接 2 个上游（双输入模式），
    未接第二上游时回退老模式（dataset_b_id 参数作第二库），保证存量模型不崩。
    """
    errs = validate_pipeline(pipeline)
    if errs:
        raise HTTPException(422, "管线校验未通过：" + "；".join(errs[:6]))
    nodes = {n["id"]: n for n in pipeline["nodes"]}
    order = topo_order(pipeline)
    # 上游映射：to -> [(from, to_port)]，按 port 排序
    in_edges: dict = {}
    for e in pipeline.get("edges") or []:
        a, b = e.get("from"), e.get("to")
        if a is None or b is None:
            continue
        port = e.get("to_port", 0) or 0
        in_edges.setdefault(b, []).append((a, port))
    for b in in_edges:
        in_edges[b].sort(key=lambda x: x[1])
    df_map: dict = {}
    steps = []
    names: dict = {}
    src_names: dict = {}   # source 节点 id -> 数据集名
    src_of: dict = {}      # 任意节点 id -> 其数据溯源的 source 节点 id

    def _src_name(nid: str) -> str:
        return src_names.get(src_of.get(nid, nid), "数据集")

    for nid in order:
        n = nodes[nid]
        t = n["type"]
        ups = [f for (f, _p) in in_edges.get(nid, [])]
        if t == "source":
            df, na = _load_dataset(db, int(n["dataset_id"]))
            df_map[nid] = df
            src_names[nid] = na
            src_of[nid] = nid
            if "a" not in names:
                names["a"] = na
        elif t == "filter":
            if not ups:
                return {"hits": [], "steps": steps, "hit_rows": []}
            df_map[nid] = _apply_filter(df_map[ups[0]], n)
            src_of[nid] = src_of.get(ups[0], ups[0])
        elif t == "join":
            if not ups:
                return {"hits": [], "steps": steps, "hit_rows": []}
            dfa = df_map[ups[0]]
            if len(ups) >= 2:
                dfb = df_map[ups[1]]
                df_map[nid] = _apply_join_df(dfa, dfb, n, names,
                                             _src_name(ups[0]), _src_name(ups[1]))
            else:
                df_map[nid] = _apply_join(db, dfa, n, names)
            src_of[nid] = src_of.get(ups[0], ups[0])
        elif t == "aggregate":
            if not ups:
                return {"hits": [], "steps": steps, "hit_rows": []}
            df_map[nid] = _apply_aggregate(df_map[ups[0]], n)
            src_of[nid] = src_of.get(ups[0], ups[0])
        elif t == "threshold":
            if not ups:
                return {"hits": [], "steps": steps, "hit_rows": []}
            df_map[nid] = _apply_threshold(df_map[ups[0]], n)
            src_of[nid] = src_of.get(ups[0], ups[0])
        elif t == "output":
            if not ups or df_map.get(ups[0]) is None:
                return {"hits": [], "steps": steps, "hit_rows": []}
            df = df_map[ups[0]]
            hits = _make_hits(df, n, names)
            steps.append({"node_id": nid, "type": t, "label": NODE_TYPES[t]["label"],
                          "rows": len(df), "hits": len(hits)})
            return {"hits": hits, "steps": steps, "hit_rows": hits}
        df = df_map.get(nid)
        if df is None:
            continue
        if len(df) > MAX_ROWS:
            raise HTTPException(422, f"节点「{NODE_TYPES.get(t, {}).get('label', t)}」后行数 {len(df)} 超过上限 {MAX_ROWS}，请收紧条件")
        if collect_steps:
            steps.append({"node_id": nid, "type": t, "label": NODE_TYPES[t]["label"], "rows": len(df)})
    return {"hits": [], "steps": steps, "hit_rows": []}


def describe_pipeline(pipeline: dict) -> str:
    """人类可读的管线摘要（存模型 description）。"""
    nodes = {n["id"]: n for n in pipeline.get("nodes") or []}
    edges = pipeline.get("edges") or []
    in_cnt: dict = {}
    for e in edges:
        in_cnt[e.get("to")] = in_cnt.get(e.get("to"), 0) + 1
    parts = []
    for nid in topo_order(pipeline) if pipeline.get("nodes") else []:
        n = nodes[nid]
        t = n["type"]
        if t == "source":
            parts.append(f"数据源#{n.get('dataset_id')}")
        elif t == "filter":
            parts.append(f"过滤[{n.get('field')} {n.get('op')} {n.get('value')}]")
        elif t == "join":
            r = {"intersection": "两库交集", "duplicate": "同库重复", "inclusion": "包含命中"}.get(n.get("rule"), n.get("rule"))
            if n.get("dataset_b_id"):
                extra = f" ×数据集B#{n.get('dataset_b_id')}"
            elif in_cnt.get(nid, 0) >= 2:
                extra = " ←第二上游"
            else:
                extra = ""
            parts.append(f"对碰[{r} 键:{n.get('key_a')}{extra}]")
        elif t == "aggregate":
            parts.append(f"聚合[{n.get('group_by')} {n.get('agg', 'count')}{('/' + n['agg_field']) if n.get('agg_field') else ''}]")
        elif t == "threshold":
            parts.append(f"阈值[{n.get('field') or 'agg_result'} {n.get('op')} {n.get('value')}]")
        elif t == "output":
            parts.append(f"输出[键:{n.get('key_field')}]")
    return " → ".join(parts) or "空管线"
