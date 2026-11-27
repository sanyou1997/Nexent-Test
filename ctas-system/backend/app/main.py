import os

# 规避 onnxruntime 与 opencv 同时内置 libomp140.dll 的 OpenMP 重复库冲突
# （该冲突会导致 OCR / ASR-VAD 原生崩溃 segfault）。放在任何第三方 import 之前。
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging
import traceback
from fastapi.staticfiles import StaticFiles
from app.config import BASE_DIR, settings
from app.database import Base, engine, SessionLocal, install_tenancy
from app import seed, migrate, demo_data, tenancy
from app.routers import (auth, users, regulations, qa, petitions, documents,
                         auditlog, ocr, asr, stats, transcripts, bank, verify,
                         cases, trials, conductor, inspections,
                         supervise, alerts, clue, integrity, assistant, education, archives,
                         datasets, appeals, system, data_intake, historical_reports, nexent, recognition, workbench,
                         trial_materials, departments)

install_tenancy()  # 部门隔离：ORM 统一过滤 + 新建记录自动归属


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    migrate.migrate(engine)  # 旧库补列（如 is_official）
    changed = migrate.migrate_tenancy(engine)  # 部门隔离：dept_id 列 / 部门内唯一
    if changed:
        print(f"[tenancy-migrate] {changed}")
    migrate.dedup_partial_regulations(engine)  # 残本法规去重（网页提炼丢条事故善后）
    db = SessionLocal()
    try:
        seed.run(db)
        info = demo_data.run(db)  # 法规全集 + 审理示范案件（幂等）
        if any(info.values()):
            print(f"[demo-data] {info}")
        t = tenancy.bootstrap(db)  # 默认部门、账号归部门、历史数据归属、移交授权（幂等）
        if any(t.values()):
            print(f"[tenancy] {t}")
    finally:
        db.close()
    yield

app = FastAPI(title="纪检监察业务辅助平台", version="2.0", lifespan=lifespan)


# ---- 全局异常处理：统一错误响应结构 + 服务端日志 ----
# 避免路由未捕获异常时前端只收到裸「Internal Server Error」，并保留堆栈便于排查。
_logger = logging.getLogger("jiwei")
if not _logger.handlers:
    logging.basicConfig(level=logging.INFO)

@app.exception_handler(Exception)
async def _unhandled_exc(request: Request, exc: Exception):
    _logger.error("Unhandled %s %s: %s\n%s", request.method, request.url.path, exc, traceback.format_exc())
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误，请稍后重试（详情见服务端日志）", "code": "INTERNAL_ERROR"},
    )

@app.exception_handler(StarletteHTTPException)
async def _http_exc(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": "HTTP_ERROR"})

@app.exception_handler(RequestValidationError)
async def _validation_exc(request: Request, exc: RequestValidationError):
    # 不回显原始输入（可能是 bytes / 敏感字段），只给出字段位置与原因，避免序列化失败导致连接被重置
    errors = [{"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
              for e in exc.errors()]
    return JSONResponse(
        status_code=422,
        content={"detail": "请求参数校验失败", "errors": errors, "code": "VALIDATION_ERROR"},
    )


# 轻量迁移与数据保洁：见 app/migrate.py


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

for r in (auth.router, users.router, regulations.router, qa.router,
          petitions.router, documents.router, auditlog.router,
          ocr.router, asr.router, stats.router, transcripts.router, bank.router,
          verify.router, cases.router, trials.router, conductor.router,
          inspections.router, supervise.router, alerts.router, clue.router,
          integrity.router, assistant.router, education.router, archives.router,
          datasets.router, appeals.router, system.router,
          data_intake.router, historical_reports.router, nexent.router, recognition.router, workbench.router,
          trial_materials.router, departments.router):
    app.include_router(r)

@app.get("/api/health")
def health():
    return {"ok": True, "app": "纪检监察业务辅助平台", "version": "2.0"}


# ---- 前端静态托管（打包部署模式：BASE_DIR/web 下存在构建产物时启用）----
WEB_DIR = BASE_DIR / settings.WEB_DIR_NAME
if WEB_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str, request: Request):
        # 命中真实文件则返回文件，否则回退 index.html（SPA 路由刷新支持）
        target = (WEB_DIR / full_path).resolve()
        if (full_path and target.is_file()
                and str(target).startswith(str(WEB_DIR.resolve()))):
            return FileResponse(target)
        # index.html 不缓存：升级后浏览器立即拿到新的带哈希资源清单（assets/ 下文件名含哈希，可长期缓存）
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
