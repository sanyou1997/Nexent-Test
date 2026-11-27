"""向量层：
- 配置 EMBEDDING_API_KEY（如硅基流动 BGE-M3）→ 云端稠密向量 + 余弦相似度；
- 未配置 → 本地字符 n-gram 稀疏向量（TF-IDF 余弦），零依赖、零网络，中文短文本检索效果可满足演示。
两模式接口同构，法规入库与检索共用。
"""
import math, re
from collections import Counter
from app.config import settings


def tokenize(text: str) -> list[str]:
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text)
    tokens = [text[i:i+2] for i in range(len(text) - 1)]
    tokens += [text[i:i+3] for i in range(len(text) - 2)]
    return [t for t in tokens if t]


def embed_local(text: str) -> dict:
    """本地稀疏向量：{token: tf}。"""
    c = Counter(tokenize(text))
    total = sum(c.values()) or 1
    return {k: v / total for k, v in c.items()}


def embed_dense(texts: list[str]) -> list[list[float]] | None:
    """云端稠密向量（可选）。"""
    if not settings.EMBEDDING_API_KEY:
        return None
    from openai import OpenAI
    client = OpenAI(api_key=settings.EMBEDDING_API_KEY, base_url=settings.EMBEDDING_BASE_URL)
    try:
        resp = client.embeddings.create(model=settings.EMBEDDING_MODEL, input=texts)
        return [d.embedding for d in resp.data]
    except Exception:
        return None


def cosine_sparse(a: dict, b: dict, idf: dict | None = None) -> float:
    if not a or not b:
        return 0.0
    if idf is None:
        idf = {}
    small, big = (a, b) if len(a) <= len(b) else (b, a)
    dot = sum(w * big.get(t, 0.0) * idf.get(t, 1.0) * idf.get(t, 1.0) for t, w in small.items())
    na = math.sqrt(sum((w * idf.get(t, 1.0)) ** 2 for t, w in a.items()))
    nb = math.sqrt(sum((w * idf.get(t, 1.0)) ** 2 for t, w in b.items()))
    return dot / (na * nb) if na and nb else 0.0


def cosine_dense(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
