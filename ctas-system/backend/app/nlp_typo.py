"""离线 NLP 错别字检测（纯本地，无需联网，无 torch/kenlm 依赖）。

思路：复用 pycorrector 社区标注的混淆集与词频资源，但不 import pycorrector 包
（其顶层依赖 torch、运行时依赖 kenlm，在 Windows/Python3.13 离线环境无法安装），
改为直接读取其 data 目录下的数据文件：
  - same_pinyin.txt : 同音混淆集（字 -> 同音同调/异调字）
  - same_stroke.txt : 形近混淆集（字 -> 笔画相近字）
  - word_freq.txt   : 58 万条词频（用于分词 + 词级流畅度打分）

检测算法（字符级混淆 + 词级流畅度，jieba 分词）：
  对每个中文汉字，枚举其同音/形近候选字；用 jieba 对「原文局部」与「替换后局部」
  分词并以词频打分。仅当同时满足：
    ① 原文该字本身未构成任何常见词（否则如 反应/反映、经研究 不报警）；
    ② 替换为某候选字后，构成了一个常见词（词频 >= 阈值）；
    ③ 替换使局部流畅度（词频对数和）显著提升（delta 超过阈值）；
  才判定为疑似错别字。
  这样既能覆盖海量同音/形近错别字（无需手写规则），又避免对正确短语误报。

级别统一为 warn（疑似，建议核对）——确定性的高频词仍由 proofread.py 的精确词典
以 error 级别给出，两者互补。
"""
import os
import math
import threading

# ---- 阈值 ----
_WORD_MIN = 50          # 替换后构成的词需达到的常见度（词频）下限
_WORD_MIN_STROKE = 500  # 形近候选更严格（构成的词须更常见，防误报）
_DELTA = 1.0            # 词级流畅度提升（自然对数）下限
_WIN = 3                # 检测窗口半宽（前后各取 3 字）
_SMOOTH = 1.0           # 加法平滑

_lock = threading.Lock()
_PINYIN = None          # char -> set[candidate chars]
_STROKE = None          # char -> set[candidate chars]
_WF = None              # word -> count
_READY = False


def _data_dir() -> str | None:
    candidates = [os.path.join(os.sys.prefix, "Lib", "site-packages", "pycorrector", "data")]
    try:
        import site
        for s in site.getsitepackages():
            candidates.append(os.path.join(s, "pycorrector", "data"))
    except Exception:
        pass
    for d in candidates:
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "word_freq.txt")):
            return d
    return None


def _lexicon_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "domain_lexicon.txt")


def _load():
    global _PINYIN, _STROKE, _WF, _READY
    with _lock:
        if _READY:
            return
        d = _data_dir()
        if not d:
            _READY = True
            return
        pinyin: dict[str, set] = {}
        stroke: dict[str, set] = {}
        wf: dict[str, int] = {}

        with open(os.path.join(d, "same_pinyin.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                ch = parts[0]
                cands = set()
                for field in parts[1:]:
                    cands.update(field)
                if cands:
                    pinyin[ch] = cands

        with open(os.path.join(d, "same_stroke.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                ch = parts[0]
                cands = set(parts[1:])
                if cands:
                    stroke[ch] = cands

        with open(os.path.join(d, "word_freq.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                w = parts[0]
                try:
                    c = int(parts[1])
                except ValueError:
                    continue
                if c <= 0:
                    continue
                prev = wf.get(w)
                if prev is None or c > prev:
                    wf[w] = c

        _PINYIN, _STROKE = pinyin, stroke
        _WF = wf

        # 受保护领域术语：加入 jieba 分词 + 注入高词频，避免把正确纪检术语误纠错
        try:
            import jieba
            lex = _lexicon_path()
            if os.path.exists(lex):
                with open(lex, encoding="utf-8") as lf:
                    for line in lf:
                        t = line.strip()
                        if not t or t.startswith("#"):
                            continue
                        jieba.add_word(t, freq=10 ** 9)
                        _WF[t] = max(_WF.get(t, 0), 10 ** 9)
        except Exception:
            pass

        _READY = True


def _is_cjk(c: str) -> bool:
    return "\u4e00" <= c <= "\u9fff"


def _score_tokens(tokens) -> float:
    s = 0.0
    for t in tokens:
        s += math.log(_WF.get(t, 0) + _SMOOTH)
    return s


def _formed_word(tokens, ch, min_count):
    """返回包含 ch 且频次 >= min_count 的最长词（及其频次），无则 (None, 0)。"""
    best_w, best_c = None, 0
    for t in tokens:
        if len(t) >= 2 and ch in t:
            c = _WF.get(t, 0)
            if c >= min_count and (best_c == 0 or c > best_c or (c == best_c and len(t) > len(best_w))):
                best_c, best_w = c, t
    return best_w, best_c


def detect(text: str, covered: set | None = None) -> list[dict]:
    """检测疑似错别字。

    covered: 已被精确词典命中的 (start,end) 区间集合，命中则跳过该位置，避免重复报警。
    返回 list of dict {start, end, wrong, right, word, score, source}
    """
    _load()
    if not _READY or _WF is None:
        return []
    if not text:
        return []

    try:
        import jieba
    except Exception:
        return []

    results = []
    n = len(text)
    for i, c in enumerate(text):
        if not _is_cjk(c):
            continue
        if covered and any(s <= i < e for (s, e) in covered):
            continue
        cands_pinyin = _PINYIN.get(c, set())
        cands_stroke = _STROKE.get(c, set())
        if not cands_pinyin and not cands_stroke:
            continue

        start = max(0, i - _WIN)
        end = min(n, i + _WIN + 1)
        window = text[start:end]
        li = i - start

        try:
            orig_tokens = list(jieba.cut(window))
        except Exception:
            continue
        # 原文该字已处于某个常见词中 -> 不报警（如 反应/反映、正风肃纪）
        if _formed_word(orig_tokens, c, _WORD_MIN)[1] > 0:
            continue
        base = _score_tokens(orig_tokens)

        best = None  # (improvement, right, word, source)
        for r in cands_pinyin:
            if r == c:
                continue
            repl = window[:li] + r + window[li + 1:]
            try:
                rt = list(jieba.cut(repl))
            except Exception:
                continue
            word, wcnt = _formed_word(rt, r, _WORD_MIN)
            if not word:
                continue
            imp = _score_tokens(rt) - base
            if imp > _DELTA and (best is None or imp > best[0]):
                best = (imp, r, word, "同音")
        if best is None:
            for r in cands_stroke:
                if r == c:
                    continue
                repl = window[:li] + r + window[li + 1:]
                try:
                    rt = list(jieba.cut(repl))
                except Exception:
                    continue
                word, wcnt = _formed_word(rt, r, _WORD_MIN_STROKE)
                if not word:
                    continue
                imp = _score_tokens(rt) - base
                if imp > _DELTA and (best is None or imp > best[0]):
                    best = (imp, r, word, "形近")

        if best:
            imp, r, word, source = best
            results.append({
                "start": i, "end": i + 1, "wrong": c, "right": r,
                "word": word, "score": round(imp, 2), "source": source,
            })

    # 邻近去重：相邻（<=1 字）的候选只保留构成更长/更常见词者，消除连锁误报
    results.sort(key=lambda d: (-len(d["word"]), -d["score"]))
    kept = []
    for d in results:
        if any(abs(d["start"] - k["start"]) <= 1 for k in kept):
            continue
        kept.append(d)
    return kept


if __name__ == "__main__":
    samples = [
        "他做为站长，既使困难也要按步就班分折问题，反应情况要客观，克苦钻研业务。",
        "经查该同志纪律意识淡薄，其做为部门负责人即然明知故犯，还布署下属分折帐务。",
        "根据党纪处分条例，经研究决定给予党内警告处分，并责令作出书面检查。",
        "会议指出，要认真贯彻落实中央八项规定精神，持之以恒正风肃纪。",
        "该同志能够严格遵守政治纪律和政治规矩，自觉接受组织监督。",
        "现将有关情况汇报如下，请审阅。",
    ]
    for s in samples:
        print("\n原文:", s)
        for d in detect(s):
            print(f"  -> [{d['start']}] {d['wrong']} => {d['right']}  成词「{d['word']}」({d['source']}, 提升{d['score']})")
