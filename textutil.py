import re

STOPWORDS = set("""a an the and or but if then so of to in on at for from by with about as into over under is are was were be been being
am do does did doing have has had having i me my we our you your he she it its they them their this that these those what which who whom
whose when where why how can could should would will shall may might must not no yes any some all each every more most much many very just
also than too tell explain please give need want know get got make use using used best good guide help question ask there here out up down
off again further once only own same such both few other new
maplestory classic world worlds mscw maple mapleworld find drop drops""".split())

_word = re.compile(r"[a-z0-9']+")


def stem(w: str) -> str:
    w = w.replace("'", "")
    for suf in ("ing", "es", "s"):
        if len(w) > len(suf) + 2 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def tokens(text: str, keep_stop: bool = False) -> list[str]:
    out = []
    for w in _word.findall(text.lower()):
        if not keep_stop and w in STOPWORDS:
            continue
        out.append(stem(w))
    return out


def keywords(text: str) -> list[str]:
    """Stopword-filtered, original (unstemmed) lower-case words, in order."""
    return [w for w in _word.findall(text.lower()) if w not in STOPWORDS and len(w) > 1]


def clean_ws(text: str) -> str:
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()
