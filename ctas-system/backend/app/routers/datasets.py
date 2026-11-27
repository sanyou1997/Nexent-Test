"""智能监督数据接入：上传 Excel/CSV 数据集（如公职人员名册、补贴发放表、中标人名册），
自动识别字段，存为数据集，供模型工厂做关键字段关联对碰。

支持大文件流式读取（行数上限保护），字段 schema + 行数据（JSON）一并存储。
"""
import io
import json

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.audit import write_log
from app.models import Dataset, User

router = APIRouter(prefix="/api/datasets", tags=["智能监督数据接入"])

MAX_ROWS = 50000
MAX_FILE_MB = 50


def _read_df(name: str, data: bytes):
    if not data:
        raise HTTPException(400, "上传文件为空")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {MAX_FILE_MB}MB 限制")
    try:
        if name.lower().endswith(".csv"):
            last = None
            for enc in ("utf-8-sig", "gbk", "gb18030"):
                try:
                    return pd.read_csv(io.BytesIO(data), encoding=enc, dtype=str,
                                        keep_default_na=False, on_bad_lines="skip")
                except UnicodeDecodeError as e:
                    last = e
                    continue
            raise HTTPException(422, f"CSV 编码无法识别（已尝试 UTF-8/GBK/GB18030）：{last}")
        return pd.read_excel(io.BytesIO(data), dtype=str, engine="openpyxl")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"文件解析失败：{e}")


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    name: str = "",
    note: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = await file.read()
    df = _read_df(file.filename or "", data)
    if df.empty or len(df.columns) == 0:
        raise HTTPException(422, "文件无有效数据或表头")
    df = df.dropna(how="all")
    if len(df) > MAX_ROWS:
        df = df.head(MAX_ROWS)
    columns = [str(c).strip() for c in df.columns]
    records = []
    for _, r in df.iterrows():
        rec = {columns[i]: ("" if pd.isna(r.iloc[i]) else str(r.iloc[i]).strip())
               for i in range(len(columns))}
        records.append(rec)
    ds = Dataset(name=name or (file.filename or "数据集"), source_filename=file.filename or "",
                 columns=columns, row_count=len(records), rows_json=json.dumps(records, ensure_ascii=False),
                 note=note, created_by=user.id)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    write_log(db, user_id=user.id, username=user.username, action="dataset_upload",
              resource_type="dataset", resource_id=ds.id,
              detail={"name": ds.name, "rows": ds.row_count, "columns": len(columns)})
    return {"id": ds.id, "name": ds.name, "columns": columns, "row_count": ds.row_count,
            "preview": records[:10]}


@router.get("")
def list_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Dataset).order_by(Dataset.id.desc()).all()
    uids = {s.created_by for s in rows if s.created_by}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(uids)).all()} if uids else {}
    return [{
        "id": s.id, "name": s.name, "source_filename": s.source_filename,
        "columns": s.columns, "row_count": s.row_count, "note": s.note,
        "created_by": (users.get(s.created_by).real_name if s.created_by in users else ""),
        "created_at": s.created_at.strftime("%Y-%m-%d %H:%M") if s.created_at else "",
    } for s in rows]


@router.get("/{did}")
def get_one(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = db.get(Dataset, did)
    if not s:
        raise HTTPException(404, "数据集不存在")
    rows = json.loads(s.rows_json or "[]")
    return {"id": s.id, "name": s.name, "columns": s.columns, "row_count": s.row_count,
            "preview": rows[:20], "note": s.note}


@router.delete("/{did}")
def remove(did: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    s = db.get(Dataset, did)
    if not s:
        raise HTTPException(404, "数据集不存在")
    db.delete(s)
    db.commit()
    write_log(db, user_id=user.id, username=user.username, action="dataset_delete",
              resource_type="dataset", resource_id=did)
    return {"ok": True}
