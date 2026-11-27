import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# PyInstaller 打包后，数据目录以 exe 所在目录为基准；开发时用项目根目录
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def is_local_endpoint(url: str) -> bool:
    """判断模型服务地址是否为本机/内网免鉴权推理服务（如本机 Ollama、内网 vLLM）。

    这类本地服务通常不设 API Key，因此判为 local 后不应因为没有 Key 就判成不可用。
    """
    base = (url or "").lower()
    return (
        "localhost" in base
        or "127.0.0.1" in base
        or "0.0.0.0" in base
        or base.startswith("http://192.")
        or base.startswith("http://10.")
        or base.startswith("http://172.")
        or "host.docker.internal" in base
    )


class Settings:
    APP_NAME: str = os.getenv("CTAS_APP_NAME") or "纪检监察智能化应用 Demo"
    # 数据库：默认全业务 demo.db；CTAS（审理辅助系统）经环境变量切换到独立库 ctas.db
    DB_PATH: Path = (Path(os.getenv("CTAS_DB_PATH")) if os.getenv("CTAS_DB_PATH")
                     else BASE_DIR / "data" / "demo.db")
    # 生产 / Docker：PostgreSQL（如 postgresql+psycopg://user:pwd@host:5432/db）；未设置时回落 SQLite（本地自检脚本用）
    DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip()
    CORPUS_DIR: Path = BASE_DIR / "data" / "corpus"
    # 前端静态托管目录：默认全业务 web/；CTAS 经环境变量切换到 web-ctas/
    WEB_DIR_NAME: str = os.getenv("CTAS_WEB_DIR") or "web"
    # 安全
    SECRET_KEY: str = os.getenv("SECRET_KEY", "demo-secret-change-in-production-9f2c1")
    ACCESS_TOKEN_EXPIRE_HOURS: int = 12
    # 模型接入层（OpenAI 兼容协议，可插拔）
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", os.getenv("DEEPSEEK_API_KEY", ""))
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-chat")
    # Embedding（可选，配置后自动启用云端向量；否则使用本地字符 n-gram TF-IDF）
    EMBEDDING_BASE_URL: str = os.getenv("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")
    EMBEDDING_API_KEY: str = os.getenv("EMBEDDING_API_KEY", "")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    # MinerU PDF 解析（模型目录默认包内 models/mineru，离网预置；也可用环境变量覆盖）
    _mineru_dir = BASE_DIR / "models" / "mineru"
    MINERU_MODELS_DIR: str = os.getenv("MINERU_MODELS_DIR",
                                       str(_mineru_dir) if _mineru_dir.exists() else "")
    RETRIEVE_TOP_K: int = int(os.getenv("RETRIEVE_TOP_K", "5"))
    # 拒答阈值：本地字符 n-gram 余弦（中文常见字会形成基础重叠），
    # 经验值约 0.08：低于此基本可断定题库中无相关条款，触发"无依据不回答"。
    # 已配 BGE-M3 稠密向量后可下调至 0.25~0.30，由实际效果调整。
    REFUSE_THRESHOLD: float = float(os.getenv("REFUSE_THRESHOLD", "0.08"))
    # ASR 本地语音识别模型（tiny/base/small/medium，base 平衡推荐）
    ASR_MODEL: str = os.getenv("ASR_MODEL", "base")
    # CORS 允许来源（逗号分隔）；默认本地开发常用端口，生产用环境变量覆盖
    CORS_ORIGINS: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:3000,http://127.0.0.1:3000"
    )

    @property
    def cors_origins(self) -> list[str]:
        return [s.strip() for s in self.CORS_ORIGINS.split(",") if s.strip()]

settings = Settings()
settings.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
