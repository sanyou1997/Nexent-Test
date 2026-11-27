"""离线高精度错别字检测（统计语言模型 + 混淆集 + 短语级保护）。

定位：替代原 nlp_typo 的弱规则层。原层对单字做「替换后是否构成更常见词」判断，
阈值低、且不对领域术语做短语级保护，导致大量误报（如把正确词「纪检」判为「监」、
把「按步就班」里的「班」判为「半」）。本模块在同等离线条件下显著提升精度：

  • 候选：同音混淆（same_pinyin）+ 形近混淆（same_stroke），数据来自 pycorrector 包。
  • 接受条件（全部满足才报警）：
      1) 原字在局部窗口内不构成任何常见词（否则如「纪检监察」不报警）；
      2) 替换为候选字后，构成一个明显更常见的词（词频 ≥ _STRONG，抑制弱候选）；
      3) 局部流畅度（词频对数和）提升显著（≥ _DELTA）。
  • 短语级保护：领域术语白名单（domain_lexicon.txt）+ 用户自定义关键词，命中即整段跳过，
    从根本上避免重复命中领域常用词（如「纪检」「监察」「正风肃纪」「留置」等）。

依赖：jieba（分词）+ pycorrector 数据（混淆集与 58 万词频）。无需 torch / kenlm / 联网。
注：本环境 HuggingFace 与 ModelScope 的纠错类模型均不可达，故采用该离线统计引擎作为
高精度的可行实现；若后续提供本机模型权重，可在 detect() 内无缝替换为神经网络推理。
"""
import os
import math
import threading

from app import nlp_typo

# ---- 阈值（偏向高精度，宁可漏报也不误报）----
_WORD_MIN = 50           # 替换后构成的词需达到的常见度下限（基础门槛）
_STRONG = 1000           # 替换后构成词的词频硬下限（抑制「半分」这类弱候选）
_DELTA = 2.0             # 局部流畅度提升（自然对数）下限
_WIN = 3                 # 检测窗口半宽（前后各 3 字）
_STROKE_MULT = 1.6       # 形近候选词频门槛相对 _STRONG 的倍率（形近更严格）

_lock = threading.Lock()
_PROTECTED_STATIC = None  # 领域术语白名单（受保护短语集合，启动时加载一次）


def _static_phrases() -> set:
    global _PROTECTED_STATIC
    with _lock:
        if _PROTECTED_STATIC is not None:
            return _PROTECTED_STATIC
        phrases: set = set()
        path = nlp_typo._lexicon_path()
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    t = line.strip()
                    if t and not t.startswith("#"):
                        phrases.add(t)
        except Exception:
            pass
        _PROTECTED_STATIC = phrases
        return phrases


def protect_spans(text: str, extra=None) -> set:
    """返回所有受保护短语在 text 中的 (start,end) 区间集合；命中区间内的字不参与纠错。"""
    phrases = set(_static_phrases())
    if extra:
        phrases.update(t for t in extra if t and len(t) >= 1)
    spans: set = set()
    for p in phrases:
        if len(p) < 1:
            continue
        start = 0
        while True:
            i = text.find(p, start)
            if i < 0:
                break
            spans.add((i, i + len(p)))
            start = i + 1
    return spans


def _score_tokens(tokens) -> float:
    s = 0.0
    for t in tokens:
        s += math.log(nlp_typo._WF.get(t, 0) + 1.0)
    return s


def _formed_word(tokens, ch, min_count):
    """返回包含 ch 且频次 >= min_count 的最长词（及其频次），无则 (None, 0)。"""
    best_w, best_c = None, 0
    for t in tokens:
        if len(t) >= 2 and ch in t:
            c = nlp_typo._WF.get(t, 0)
            if c >= min_count and (best_c == 0 or c > best_c or (c == best_c and len(t) > len(best_w))):
                best_c, best_w = c, t
    return best_w, best_c


def detect(text: str, protected: list | None = None, covered: set | None = None) -> list[dict]:
    """高精度错别字检测。

    protected: 用户自定义受保护关键词（额外短语/单字），命中区间跳过。
    covered: 已被精确词典命中的 (start,end) 区间，命中跳过，避免重复报警。
    返回 list of {start,end,wrong,right,word,score,source}。
    """
    nlp_typo._load()
    if not nlp_typo._READY or nlp_typo._WF is None:
        return []
    if not text:
        return []
    try:
        import jieba
    except Exception:
        return []

    spans = protect_spans(text, protected)
    results = []
    n = len(text)
    for i, c in enumerate(text):
        if not ("\u4e00" <= c <= "\u9fff"):
            continue
        if spans and any(s <= i < e for s, e in spans):
            continue
        if covered and any(s <= i < e for s, e in covered):
            continue
        cands_p = nlp_typo._PINYIN.get(c, set())
        cands_s = nlp_typo._STROKE.get(c, set())
        if not cands_p and not cands_s:
            continue

        start = max(0, i - _WIN)
        end = min(n, i + _WIN + 1)
        window = text[start:end]
        li = i - start
        try:
            orig = list(jieba.cut(window))
        except Exception:
            continue
        # 原字已处于某个常见词中 -> 不报警（如「纪检监察」「正风肃纪」）
        if _formed_word(orig, c, _WORD_MIN)[1] > 0:
            continue
        base = _score_tokens(orig)

        best = None  # (improvement, right, word, source)
        for r in cands_p:
            if r == c:
                continue
            repl = window[:li] + r + window[li + 1:]
            try:
                rt = list(jieba.cut(repl))
            except Exception:
                continue
            word, wcnt = _formed_word(rt, r, _WORD_MIN)
            if not word or wcnt < _STRONG:
                continue
            imp = _score_tokens(rt) - base
            if imp > _DELTA and (best is None or imp > best[0]):
                best = (imp, r, word, "同音")
        if best is None:
            for r in cands_s:
                if r == c:
                    continue
                repl = window[:li] + r + window[li + 1:]
                try:
                    rt = list(jieba.cut(repl))
                except Exception:
                    continue
                word, wcnt = _formed_word(rt, r, int(_STRONG * _STROKE_MULT))
                if not word or wcnt < int(_STRONG * _STROKE_MULT):
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

    # 邻近去重：相邻（<=1 字）候选只保留构成更长/更常见词者
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
        "纪检监察智能化应用能够提升信访件办理效率。",
        "根据党纪处分条例，经研究决定给予党内警告处分，并责令作出书面检查。",
        "会议指出，要认真贯彻落实中央八项规定精神，持之以恒正风肃纪。",
    ]
    for s in samples:
        print("\n原文:", s)
        for d in detect(s):
            print(f"  -> [{d['start']}] {d['wrong']} => {d['right']}  成词「{d['word']}」({d['source']}, 提升{d['score']})")
