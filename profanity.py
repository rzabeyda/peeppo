"""Russian мат detector for the public chat auto-mute (see bot.py, _mute_for_profanity).

contains_mat(text) -> True if the text has a word built on one of the main мат roots
(хуй, пизд, еб/ёб, бля/бляд, сука, муда, пидор, залуп, манда, гандон).

Before matching, the text is normalized so the usual dodges still hit:
  * ё -> е, latin lookalikes -> cyrillic (xyй, cyka, bлять), digits (3 -> з, 0 -> о, 6 -> б ...)
  * stretched letters collapse (хууууй -> хуй); words are checked both ways
  * spelled-out letters join back (х у й, б.л.я -> хуй, бля)
  * * # % stand for a hidden letter (х*й, п#зда, б**ть) — only when the word has 2+ real letters
Roots are anchored (word start / known prefixes) or exclusion-listed so normal words like
небо, учеба, себе, рубля, оскорблять, употреблять, колебаться, страхуй, психуй,
команда, мандарин, барсук, мудрый, спидран do NOT trigger.
"""
import itertools
import re

_LOOKALIKES = str.maketrans({
    "a": "а", "b": "б", "c": "с", "e": "е", "h": "н", "k": "к", "m": "м", "n": "п",
    "o": "о", "p": "р", "t": "т", "u": "и", "x": "х", "y": "у", "i": "и",
    "z": "з", "3": "з", "0": "о", "6": "б", "4": "ч", "1": "и", "@": "а", "$": "с",
    "ё": "е", "і": "и", "ї": "и", "ъ": "ь",
})

_WILDCARDS = "*#%"
_WILDCARD_FILL = "аеиоуыяюйбдзлтч"
_MAX_WILDCARDS = 3

# prefixes that turn "еб" into мат (заебал, выебываться, долбоеб...). Deliberately no bare
# "с"/"в"/"н" (себе, веб, небо) — those need the hard/soft sign: съеб, въеб.
_EB_PREFIXES = (
    "за|на|вы|у|по|до|при|пере|недо|долбо|долба|мозго|разь|рась|оть|обь|подь|изь|сь|вь|про"
)

_PATTERNS = [
    # хуй, хуя, хуе(сос), хуи, хую, нахуй, похуй, охуеть, ахуеть, нихуя, хуйня. Anchored to the word
    # start / a prefix so плохую, сухую, штрихуй, страхуй don't hit
    re.compile(r"^(?:на|по|о|а|ни|не|за|до|от|вы|при|рас|раз|с|под|об|недо|пере)?ху[йяеюи]"),
    # пизда, пиздец, спиздил, распиздяй, пезда
    re.compile(r"п[иеы]зд"),
    # еб/ёб family, anchored at word start or after a мат-forming prefix
    re.compile(rf"^(?:{_EB_PREFIXES})?еб(?:[аеиоуыяюлнкшчтсь]|$)"),
    # бля, блять, блядь, блядина, блть, разблядовка
    re.compile(r"^бля|бляд|^бля?[тд]ь$|^бл[тд]$"),
    # сука, суки, суку, сукой, сукин, сучка, сучара
    re.compile(r"^сук(?:а|и|у|е|ой|ам|ами|ах|ин|ина|ины|ину)$|^суч(?:ка|ке|ку|кой|ара|ары|ару|арой|ий)$"),
    # мудак, мудила, мудозвон, мудачье
    re.compile(r"муд(?:ак|ач|ил|оз|оеб|ищ|ень|ня)"),
    # пидор, пидар, пидр, пидорас, пидарас, педрила
    re.compile(r"пид[оа]?р|педрил"),
    # залупа (not the slang verb залупаться)
    re.compile(r"залуп(?!а[тц]|и[тш]|л)"),
    # манда, мандец, мандавошка (not мандарин/мандат/мандолина/команда)
    re.compile(r"^манд(?:а|ы|у|е|ой)$|^мандец|^мандюк|^мандавош"),
    # гандон / гондон
    re.compile(r"г[ао]нд[ао]н"),
]

# normal words that a pattern above would catch
_SAFE_RE = re.compile(
    r"спидр|лапидар|скипид|"     # спидран, лапидарный, скипидар
    r"^блях|^бляш|^блямб|"       # бляха, бляшка, блямба
    r"^ебол"                     # Эбола
)


def _normalize(text: str) -> str:
    t = text.lower().translate(_LOOKALIKES)
    # keep only letters and wildcards; everything else becomes a word separator
    return re.sub(rf"[^а-яь{re.escape(_WILDCARDS)}]+", " ", t)


def _collapse(word: str) -> str:
    """Stretched letters: хууууй -> хуй, ссука -> сука."""
    return re.sub(r"([а-я])\1+", r"\1", word)


def _words(text: str) -> list[str]:
    tokens = _normalize(text).split()
    words: list[str] = []
    run: list[str] = []
    # spelled-out letters (х у й / б.л.я): join runs of 3+ single-character tokens
    for tok in tokens + [""]:
        if len(tok) == 1:
            run.append(tok)
            continue
        if len(run) >= 3:
            words.append("".join(run))
        else:
            words.extend(run)
        run = []
        if tok:
            words.append(tok)
    return words


def _word_is_mat(word: str) -> bool:
    if _SAFE_RE.search(word):
        return False
    return any(p.search(word) for p in _PATTERNS)


def _expand_wildcards(word: str):
    n = sum(word.count(w) for w in _WILDCARDS)
    if n == 0:
        yield word
        return
    real = len(word) - n
    if real < 2 or n > _MAX_WILDCARDS:
        return
    parts = re.split(rf"[{re.escape(_WILDCARDS)}]", word)
    for fill in itertools.product(_WILDCARD_FILL, repeat=n):
        yield "".join(p + f for p, f in zip(parts, fill + ("",)))


def contains_mat(text: str | None) -> bool:
    if not text:
        return False
    for word in _words(text):
        # both as typed (переебал) and with stretched letters collapsed (хууууй)
        forms = {word, _collapse(word)}
        for candidate in itertools.chain.from_iterable(_expand_wildcards(f) for f in forms):
            if _word_is_mat(candidate):
                return True
    return False
