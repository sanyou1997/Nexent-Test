"""数据库连接：生产用 PostgreSQL（DATABASE_URL），未配置时回落单文件 SQLite（本地自检 / 测试）。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.config import settings

if settings.DATABASE_URL:
    engine = create_engine(
        settings.DATABASE_URL,
        pool_size=10, max_overflow=20, pool_pre_ping=True, pool_recycle=1800,
    )
else:
    engine = create_engine(
        f"sqlite:///{settings.DB_PATH}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _rec):
        """SQLite 并发参数：WAL 让读写不互斥，busy_timeout 让写者排队而不是立刻 database is locked。"""
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()

IS_SQLITE = engine.dialect.name == "sqlite"
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


class Base(DeclarativeBase):
    pass


def install_tenancy() -> None:
    """部门隔离钩子（ORM 统一过滤 + 新建记录自动归属）；模型加载后调用一次。"""
    from app import tenancy
    if not getattr(SessionLocal, "_tenancy_installed", False):
        tenancy.install(SessionLocal)
        SessionLocal._tenancy_installed = True
