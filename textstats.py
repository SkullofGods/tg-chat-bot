"""Разбор текста для статистики: слова, стоп-слова, мат, смех, «фирменные словечки»."""

import math
import re
from functools import lru_cache

WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]{3,}")

# Эти слова не записываются в базу вообще (список не менять: на нём основана уже собранная статистика)
RECORD_STOP_WORDS = {
    "это", "как", "что", "чтобы", "или", "его", "ее", "её", "она", "они", "оно", "при", "про", "для",
    "без", "под", "над", "тут", "там", "пока", "если", "где", "когда", "потом", "тогда", "типа",
    "даже", "ага", "нет", "тоже", "ещё", "еще", "только", "очень", "мне",
    "тебе", "тебя", "меня", "нас", "вам", "вот", "чето", "какой", "какая",
    "который", "которая", "все", "всё", "будет", "было", "были", "уже",
}

# А эти хранятся, но не показываются в топах слов — слишком скучные
DISPLAY_STOP_WORDS = RECORD_STOP_WORDS | set("""
эта этот эти этой этом этого этому этим этими эту так чтоб кто кого кому чем чём том тем той тех
теми тому того такой такая такое такие таких таким такую здесь сюда туда оттуда отсюда почему
потому поэтому зачем откуда куда сколько мной нам нами них ним ними ней нее неё нему него ему
им их ими свой своя свое своё свои своих своим своей своего своему себя себе собой мой моя мое
моё мои моих моим моей моего твой твоя твое твоё твои твоих твоим твоей твоего наш наша наше
наши наших нашим нашей нашего ваш ваша ваше ваши ваших вашим вашей вашего вас какое какие каким
какую каких которое которые которых всех всем всеми весь вся буду будем будешь будут был была
быть можно нужно надо может могу можешь могут мог могла могли хочу хочешь хочет хотим хотят
хотел хотела хотели есть нету будто вроде хотя также либо нибудь чего чему просто вообще сейчас
щас теперь раз два три один одна одно одни больше меньше много мало прям прямо реально кстати
короче ладно конечно наверное вообщем вобщем пусть давай давайте после через перед между около
возле вместо кроме вокруг лет день время раньше позже сегодня завтра вчера всегда никогда иногда
часто снова опять сразу почти слишком совсем точно правда хорошо плохо лучше хуже ничего никто
нигде ничем никого тобой вами чья чей чьё чьи ведь вон мол уж знаю знаешь знает понимаю
понимаешь думаю думаешь кажется видимо говорю говорит сказал сказала делать сделать делаю делает
стал стала стало стали стать человек люди людей всего целом часть сам сама само сами самый
самая самое самые чуть немного немножко чтото буквально обычно именно равно скорее самом деле
хоть тот каждый человека какой-то чисто вообще-то итак значит нас просто-напросто
the and you for that this with are not but was have just what like
""".split())

_SWEAR_RE = re.compile(
    r"^(?:"
    r"(?:за|у|до|вы|про|на|отъ|съ|по|подъ|разъ|изъ|пере|при|о|недо)?[её]б(?:[аулинтёеы]|$)"
    r"|(?:на|по|от|до|за|ни|о)?ху[йеёяюи]"
    r"|.*пизд"
    r"|бля"
    r"|сук(?:а|и|у|ой|ам|ами|ах|е)?$"
    r"|суч(?:к|ар|ий|ь)"
    r"|муд(?:ак|ач|ил|оз)"
    r"|пид(?:о|а)?р"
    r"|.*залуп"
    r"|шлюх"
    r")"
)
_LAUGH_LETTERS_RE = re.compile(r"[ахпъ]+")
_LAUGH_RE = re.compile(
    r"(?:хе){2,}х?|(?:хи){2,}х?|а?(?:за){2,}з?|лол+|кек+|ору+|a?(?:ha){2,}h?|lol+|lmao+|kek+"
)


_URL_RE = re.compile(r"(https?://|www\.|t\.me/|tg://)", re.IGNORECASE)


def extract_words(text: str) -> list[str]:
    words = [w.lower() for w in WORD_RE.findall(text or "")]
    return [w for w in words if w not in RECORD_STOP_WORDS]


def tokenize(text: str) -> list[str]:
    """Слова для генератора фраз: без @упоминаний (чтобы никого не пинговать) и ссылок."""
    tokens = [
        token for token in (text or "").split()
        if not token.startswith("@") and not _URL_RE.search(token) and len(token) <= 40
    ]
    return tokens[:60]


def is_corpus_worthy(text: str) -> bool:
    """Какие сообщения запоминать для генератора фраз: без ссылок, команд и простыней."""
    if not text or text.startswith("/") or len(text) > 500 or _URL_RE.search(text):
        return False
    return len(tokenize(text)) >= 2


@lru_cache(maxsize=200_000)
def word_category(word: str) -> str | None:
    """'swear' — мат, 'laugh' — смех, 'tajik' — таджикистан, None — обычное слово."""
    if word.startswith("таджик"):
        return "tajik"
    if len(word) >= 4 and _LAUGH_LETTERS_RE.fullmatch(word) and word.count("х") >= 2:
        return "laugh"
    if _LAUGH_RE.fullmatch(word):
        return "laugh"
    if _SWEAR_RE.match(word):
        return "swear"
    return None


def is_boring(word: str) -> bool:
    return word in DISPLAY_STOP_WORDS or word.isdigit()


_ENDINGS = sorted(
    "ами ями ого его ому ему ыми ими ой ей ом ем ам ям ах ях ов ев ую юю ая яя ое ее ые ие ый ий ы и а я е у ю о ь".split(),
    key=len,
    reverse=True,
)


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def top_words(words: dict[str, int], limit: int) -> list[tuple[str, int]]:
    ranked = sorted(words.items(), key=lambda item: (-item[1], -len(item[0]), item[0]))
    return [(w, n) for w, n in ranked if not is_boring(w)][:limit]


def distinctive_words(user_words: dict[str, int], chat_words: dict[str, int], limit: int = 5) -> list[str]:
    """Слова, которые человек говорит заметно чаще остальных."""
    user_total = sum(user_words.values())
    rest_total = sum(chat_words.values()) - user_total
    if user_total < 300 or rest_total < 1000:
        return []
    scored = []
    for word, count in user_words.items():
        if count < 5 or len(word) < 4 or is_boring(word) or word_category(word) == "laugh":
            continue
        others = max(chat_words.get(word, count) - count, 0)
        lift = (count / user_total) / ((others + 1) / rest_total)
        if lift >= 4:
            scored.append((count * math.log(lift), word))
    scored.sort(reverse=True)
    result, stems = [], set()
    for _, word in scored:
        stem = _stem(word)
        if stem in stems:
            continue
        stems.add(stem)
        result.append(word)
        if len(result) == limit:
            break
    return result


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """plural(5, ("сообщение", "сообщения", "сообщений")) -> "сообщений"."""
    n = abs(n) % 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    if n == 1:
        return forms[0]
    if 2 <= n <= 4:
        return forms[1]
    return forms[2]


def fmt_num(n: int | float) -> str:
    if isinstance(n, float):
        return f"{n:.1f}".replace(".", ",")
    return f"{n:,}".replace(",", " ")


def count_with_word(n: int, forms: tuple[str, str, str]) -> str:
    return f"{fmt_num(n)} {plural(n, forms)}"
