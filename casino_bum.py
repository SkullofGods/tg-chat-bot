"""Толян: тамагочи, в которого вкладывают таджикоины, а он приносит доход.

Доход капает сам по себе и копится, пока его не заберут, но не больше CAP_HOURS часов — так что
заходить приходится. Голодный работает вполсилы. Вложения поднимают уровень: от картонной коробки
до самого богатого человека Таджикистана. Поначалу приносит гроши, изредка — находку до 500, а вот с вложениями
доход растёт всерьёз.

Образы — одежда для Толяна. Одни покупаются, другие открываются сами: за уровень, за ачивку или за особое
действие где-нибудь в казино. Каждый образ что-то меняет: больше денег, реже есть, чаще находить — часто
с подвохом (денег больше, но и жрёт чаще). Надет всегда один.
"""

import random
import time

import casino_engine as engine
import texts
from casino_engine import GameError, money
from loader import db
from textstats import count_with_word, fmt_num

# Уровень: (название, эмодзи, цена подъёма на него, доход в час)
LEVELS = [
    ("У подъезда", "🧔", 0, 12),
    ("Картонная коробка", "📦", 400, 22),
    ("Тележка из супермаркета", "🛒", 700, 40),
    ("Кроссовки", "👟", 1_200, 72),
    ("Зонт от дождя", "☔", 2_100, 130),
    ("Собака Шарик", "🐕", 3_600, 230),
    ("Палатка", "🏕", 6_200, 410),
    ("Пункт приёма стекла", "♻️", 10_500, 720),
    ("Мангал", "🔥", 18_000, 1_300),
    ("Шаурмичная", "🌯", 31_000, 2_300),
    ("Сеть шаурмичных", "🏪", 53_000, 4_200),
    # дальше доход растёт медленнее цены: это уже не про окупаемость, а про статус
    ("Чайхана", "🫖", 90_000, 6_500),
    ("Свой базар", "🏬", 150_000, 9_000),
    ("Бюро переводов", "📜", 250_000, 12_000),
    ("Своя авиакомпания", "🛩", 420_000, 15_500),
    ("Самый богатый человек Таджикистана", "👑", 700_000, 20_000),
]
CAP_HOURS = 6              # больше шести часов выручка не копится
FEED_COST = 100
FEED_HOURS = 8
HUNGRY_RATE = 0.4          # голодный приносит меньше
FIND_CHANCE = 0.12         # шанс находки при получении выручки
TROUBLE_CHANCE = 0.08      # …и шанс, что часть выручки отберут
TROUBLE_SHARE = 0.6
MIN_HOURS_FOR_LUCK = 1 / 6  # за десять минут ничего интересного не случается
FEED_FROM = 400            # с какой находки о ней узнаёт беседа
STARVED_HOURS = 24         # столько проголодал — и «Доходяга» твой
SKIN_FEED_FROM = 25_000    # о покупке образа дороже этого узнаёт лента

# Образы. Как открыть: ("coins", цена) — купить; ("level", уровень) — дорастить Толяна;
# ("award", ачивка, звёзд) — взять ачивку; ("deed", что сделать, откуда число, сколько нужно) — особое действие.
# Откуда число: ("stat", игра, поле) — статистика казино, ("counter", ключ) — счётчик игрока,
# ("ending", история, концовка) — найденная концовка, ("arcade", игра) — личный рекорд.
SKIN_KINDS = (
    ("coins", "💰", "За монетки"),
    ("level", "📈", "За прокачку"),
    ("award", "🏅", "За ачивки"),
    ("deed", "⚡", "За особые действия"),
)
SKINS = (
    ("adidas", ("coins", 2_500), "Адидас с рынка", "🏃", "Три полоски, как у настоящего пацана. Четвёртая отклеилась"),
    ("ushanka", ("coins", 4_000), "Ушанка и телогрейка", "🧤", "Подъезд не отапливается, а Толян — да"),
    ("chapan", ("coins", 6_000), "Чапан и тюбетейка", "🧣", "Как на свадьбе у двоюродного брата"),
    ("biker", ("coins", 12_000), "Байкер", "🏍", "Мотоцикла нет, зато кожанка настоящая"),
    ("boss", ("coins", 25_000), "Деловой", "💼", "Костюм одолжил у Лёни. Ушивали вчетвером"),
    ("king", ("coins", 150_000), "Царь подъезда", "🤴", "Корона из фольги от шаурмы, мантия из занавески"),

    ("janitor", ("level", 3), "Дворник", "🧹", "Метёт двор, пока никто не видит, и берёт за это чаевые"),
    ("tourist", ("level", 6), "Турист", "🎒", "Живёт в палатке и называет это глэмпингом"),
    ("griller", ("level", 8), "Шашлычник", "🍢", "Мясо от заказчика, дым от Толяна"),
    ("shawarma", ("level", 9), "Шаурмен", "🌯", "Заворачивает быстрее, чем ты успеешь передумать"),
    ("pilot", ("level", 14), "Командир воздушного судна", "👨‍✈️", "Москва — Душанбе без пересадок и без документов"),
    ("bai", ("level", 15), "Бай", "💰", "Самый богатый человек Таджикистана, и это официально"),

    ("lenya", ("award", "bum_feed", 3), "Как Лёня", "🫃", "Столько плова ещё никто не съедал. Пиджак не сходится"),
    ("ludoman", ("award", "games", 2), "Лудоман", "🎰", "Ещё одна ставка — и точно отыграется"),
    ("worker", ("award", "work_shift", 3), "Ударник труда", "⛑", "Двести смен без перекура. Ну, почти"),
    ("general", ("award", "stars", 2), "Генерал ачивок", "🎖", "Медалей больше, чем у всего подъезда вместе"),
    ("mecenat", ("award", "bum_invest", 3), "Меценат", "🎩", "В него вложили столько, что он теперь сам вкладывает"),
    ("oligarch", ("award", "rich", 2), "Олигарх", "🥂", "Шуба, цепь и ни одного налога"),
    ("sponsor", ("award", "donate", 1), "Спонсор казана", "☕", "Скинулся на казан — казан скинулся на худи"),

    ("golden", ("deed", "Сорвать джекпот 7️⃣7️⃣7️⃣ в слотах", ("stat", "slots", "special"), 1),
     "Золотой Толян", "✨", "Три семёрки — и Толян позолотел целиком"),
    ("ghost", ("deed", "Поймать патрон в русской рулетке", ("stat", "rr", "special"), 1),
     "Едва присутствует", "👻", "Вроде тут, а вроде уже и нет"),
    ("lucky", ("deed", "Пройти пустой барабан до шестого щелчка", ("counter", "rr:empty"), 1),
     "Везунчик", "🍀", "Шесть щелчков — и ни одной дырки"),
    ("ninja", ("deed", "Стать ниндзя в истории «Клан Трусыгава»", ("ending", "trusygava", "end_ninja"), 1),
     "Ниндзя Трусыгава", "🥷", "Трусы на голове, нунчаки из гольфов"),
    ("dino", ("deed", "Набрать 50 очков в «Ногозавре»", ("arcade", "nogozavr"), 50),
     "Ногозавр", "🦖", "Пришельцы украли ему ноги, он украл у них плиту"),
    ("corn", ("deed", "Наебать Ярика по полной: все шесть сделок", ("counter", "work:top:yarik"), 1),
     "Кукурузный король", "🌽", "Ярик до сих пор пересчитывает сдачу"),
    ("beaten", ("deed", "Пережить пять ограблений Толяна", ("counter", "bum:robbed"), 5),
     "Побитый жизнью", "🩹", "Опять отжали выручку. Опять пластырь"),
    ("skinny", ("deed", f"Не кормить Толяна больше {STARVED_HOURS} часов", ("counter", "bum:starved"), 1),
     "Доходяга", "🦴", "Сутки без плова. Ветром сдувает"),
)

# Бонусы образов: чем Толян в образе отличается от обычного. Чего нет в словаре — как у всех.
#   income — множитель выручки; cap — сколько часов копится выручка;
#   feed_hours / feed_cost — на сколько хватает плова и сколько он стоит; hungry — сколько приносит голодный;
#   find / find_mult — шанс находки и во сколько раз она крупнее; trouble / keep — шанс ограбления
#   и какая доля выручки после него остаётся.
BASE_PERK = {"income": 1.0, "cap": CAP_HOURS, "feed_hours": FEED_HOURS, "feed_cost": FEED_COST,
             "hungry": HUNGRY_RATE, "find": FIND_CHANCE, "find_mult": 1.0, "trouble": TROUBLE_CHANCE,
             "keep": TROUBLE_SHARE}
SKIN_PERKS = {
    "adidas": {"trouble": 0.04},                        # пацана в адидасе грабить стрёмно
    "ushanka": {"feed_hours": 12},                      # в тепле и есть меньше хочется
    "chapan": {"income": 1.10, "feed_hours": 6},        # уважаемый человек: денег больше, но и плова чаще
    "biker": {"cap": 9},                                # объезжает точки — копит дольше
    "boss": {"income": 1.15},
    "king": {"income": 1.25, "feed_cost": 300},         # царю плов подают дороже
    "janitor": {"find": 0.20},                          # метёт двор — находит чаще
    "tourist": {"find_mult": 1.5},                      # гуляет дальше — находки крупнее
    "griller": {"feed_cost": 50},                       # сыт своим шашлыком
    "shawarma": {"hungry": 0.7},                        # голодным перекусывает шаурмой
    "pilot": {"cap": 10},                               # рейсы длинные
    "bai": {"income": 1.25, "trouble": 0.0},            # на бая никто не полезет
    "lenya": {"feed_hours": 24, "feed_cost": 150},      # наедается на сутки вперёд
    "ludoman": {"find_mult": 2.0, "trouble": 0.16},     # ва-банк
    "worker": {"income": 1.15, "feed_hours": 6},        # пашет и жрёт
    "general": {"income": 1.05, "trouble": 0.0},
    "mecenat": {"income": 1.20},
    "oligarch": {"income": 1.20, "keep": 0.4},          # денег больше — и отбирают больше
    "sponsor": {"income": 1.10, "feed_hours": 10},
    "golden": {"find": 0.30},
    "ghost": {"income": 0.85, "feed_hours": 16},        # едва присутствует: и ест, и приносит меньше
    "lucky": {"find": 0.20, "trouble": 0.04},
    "ninja": {"find": 0.16, "trouble": 0.0},
    "dino": {"income": 1.20, "feed_hours": 5},          # жрёт как динозавр
    "corn": {"income": 1.15, "trouble": 0.12},          # продаёт втридорога — и грабят чаще
    "beaten": {"keep": 0.9},                            # уже нечего отбирать
    "skinny": {"hungry": 0.8},                          # привык голодать
}


def _now() -> int:
    return int(time.time())


def _bum(chat_id: int, user_id: int, now: int) -> dict:
    """Толян игрока. Новый достаётся сытым — чтобы сразу было видно, как он работает."""
    return db.get_bum(chat_id, user_id, texts.BUM_NAME, now, now + FEED_HOURS * 3600)


def _perk(skin: str | None) -> dict:
    """Правила для Толяна в этом образе (без образа — обычные)."""
    return BASE_PERK | SKIN_PERKS.get(skin, {})


def _pending(bum: dict, now: int, perk: dict) -> tuple[int, float]:
    """Сколько накопилось и за сколько часов (сытое время считается полностью, голодное — вполсилы)."""
    rate = LEVELS[bum["level"]][3] * perk["income"]
    until = min(now, bum["collected_at"] + perk["cap"] * 3600)
    if until <= bum["collected_at"]:
        return 0, 0.0
    hours = (until - bum["collected_at"]) / 3600
    fed_hours = max(0, min(until, bum["fed_until"]) - bum["collected_at"]) / 3600
    return int(rate * (fed_hours + (hours - fed_hours) * perk["hungry"])), hours


def _level_view(index: int) -> dict:
    title, emoji, cost, income = LEVELS[index]
    return {"level": index, "title": title, "emoji": emoji, "cost": cost, "income": income}


def _state(bum: dict, story: str | None = None, wardrobe: bool = False) -> dict:
    now = _now()
    perk = _perk(db.worn_bum_skin(bum["chat_id"], bum["user_id"]))
    pending, hours = _pending(bum, now, perk)
    view = _level_view(bum["level"])
    extra = {"wardrobe": _wardrobe(bum)} if wardrobe else {}
    return extra | {
        "name": texts.BUM_NAME,
        "now": now,
        "level": view,
        "levels": [_level_view(index) for index in range(len(LEVELS))],
        "next": _level_view(bum["level"] + 1) if bum["level"] + 1 < len(LEVELS) else None,
        "top": bum["level"] + 1 == len(LEVELS),
        "pending": pending,
        "hours": round(hours, 3),
        "income": view["income"] * perk["income"],     # в час, с бонусом образа
        "cap_hours": perk["cap"],
        "hungry_rate": perk["hungry"],
        "collected_at": bum["collected_at"],
        "fed_until": bum["fed_until"],
        "hungry": bum["fed_until"] <= now,
        "mood": texts.BUM_HUNGRY if bum["fed_until"] <= now else texts.BUM_FED,
        "feed_cost": perk["feed_cost"],
        "feed_hours": perk["feed_hours"],
        "invested": bum["invested"],
        "earned": bum["earned"],
        "balance": db.get_balance(bum["chat_id"], bum["user_id"]),
        "story": story,
    }


def bum_state(chat_id: int, user_id: int, wardrobe: bool = False) -> dict:
    """Толян игрока. Гардероб нужен только его экрану — лобби обходится без него."""
    now = _now()
    return _state(_bum(chat_id, user_id, now), wardrobe=wardrobe)


def _cash_out(bum: dict, now: int, perk: dict) -> tuple[int, str | None]:
    """Забирает накопленное — с находками и ограблениями, как кнопка «Забрать». Возвращает сумму и историю.
    Ничего не накопилось — время не обнуляем: пусть копится дальше."""
    chat_id, user_id = bum["chat_id"], bum["user_id"]
    pending, hours = _pending(bum, now, perk)
    story = None
    if hours >= MIN_HOURS_FOR_LUCK:
        roll = random.random()
        if roll < perk["find"]:
            # находки редкие и обычно скромные, но иногда попадается кошелёк потолще
            bonus = random.choice([random.randint(50, 150)] * 6      # обычно мелочь…
                                  + [random.randint(150, 300)] * 3
                                  + [random.randint(300, 500)])           # …и совсем редко настоящий кошелёк
            bonus = int(bonus * perk["find_mult"])
            pending += bonus
            luck = f"{random.choice(texts.BUM_FINDS)}: +{money(bonus)}"
            story = f"🎁 {texts.BUM_NAME} {luck}"
            if bonus >= FEED_FROM:
                engine.add_feed(chat_id, f"🧔 {texts.BUM_NAME} у {engine.player_name(user_id)} {luck}")
        elif roll < perk["find"] + perk["trouble"] and pending > 0:
            lost = pending - int(pending * perk["keep"])
            pending -= lost
            story = f"😔 {random.choice(texts.BUM_TROUBLES)}: −{money(lost)}"
            db.bump_counter(chat_id, user_id, "bum:robbed")   # пять ограблений — и образ «Побитый жизнью»
    if pending > 0:
        db.collect_bum(chat_id, user_id, pending, now)
    return max(0, pending), story


def collect(chat_id: int, user_id: int) -> dict:
    engine.throttle(chat_id, user_id)
    now = _now()
    bum = _bum(chat_id, user_id, now)
    paid, story = _cash_out(bum, now, _perk(db.worn_bum_skin(chat_id, user_id)))
    if paid <= 0:
        return _state(bum, story or "Пока пусто — зайди попозже", wardrobe=True)
    return _state(_bum(chat_id, user_id, now), story or f"💰 Выручка в кармане: +{money(paid)}", wardrobe=True)


def feed(chat_id: int, user_id: int) -> dict:
    engine.throttle(chat_id, user_id)
    now = _now()
    bum = _bum(chat_id, user_id, now)
    perk = _perk(db.worn_bum_skin(chat_id, user_id))
    starved = now - bum["fed_until"] >= STARVED_HOURS * 3600
    if not db.feed_bum(chat_id, user_id, perk["feed_cost"], max(now, bum["fed_until"]) + perk["feed_hours"] * 3600):
        raise engine.not_enough_money(chat_id, user_id)
    db.bump_counter(chat_id, user_id, "bum:feed")
    if starved:
        db.bump_counter(chat_id, user_id, "bum:starved")
        return _state(_bum(chat_id, user_id, now), f"🍲 Набросился на плов: голодал больше {STARVED_HOURS} часов",
                      wardrobe=True)
    hours = count_with_word(perk["feed_hours"], ("час", "часа", "часов"))
    return _state(_bum(chat_id, user_id, now), f"🍲 Поел плова. Сыт ещё {hours}", wardrobe=True)


def upgrade(chat_id: int, user_id: int) -> dict:
    engine.throttle(chat_id, user_id)
    now = _now()
    bum = _bum(chat_id, user_id, now)
    if bum["level"] + 1 >= len(LEVELS):
        raise GameError("Дальше некуда: это уже бизнес-империя", 409)
    title, emoji, cost, income = LEVELS[bum["level"] + 1]
    pending, _ = _pending(bum, now, _perk(db.worn_bum_skin(chat_id, user_id)))  # накопленное не сгорает
    if not db.upgrade_bum(chat_id, user_id, cost, pending, now):
        raise engine.not_enough_money(chat_id, user_id)
    if cost >= 10_000:
        engine.add_feed(chat_id, f"{emoji} {texts.BUM_NAME} у {engine.player_name(user_id)} дорос до «{title}»")
    title = title[:1].lower() + title[1:]   # «Собака Шарик» → «собака Шарик», имена остаются с большой
    return _state(_bum(chat_id, user_id, now), f"{emoji} Теперь это {title}: {fmt_num(income)} 🪙/час", wardrobe=True)


# ── Образы ────────────────────────────────────────────────────────────────────


def _find_skin(key) -> tuple:
    skin = next((item for item in SKINS if item[0] == key), None) if isinstance(key, str) else None
    if skin is None:
        raise GameError("Такого образа нет", 404)
    return skin


def _skin_facts(chat_id: int, user_id: int, bum: dict) -> dict:
    """Всё, от чего зависят образы: уровень, ачивки, счётчики, статистика и рекорды игрока."""
    return {
        "level": bum["level"],
        "earned": db.get_achievements(chat_id, user_id),
        "counters": db.get_counters(chat_id, user_id),
        "stats": {row["game"]: row for row in db.get_casino_stats(chat_id, user_id)},
        "arcade": lambda game: db.arcade_best(chat_id, game, user_id),
    }


def _skin_progress(rule: tuple, facts: dict) -> tuple[int, int]:
    """Сколько уже сделано и сколько нужно. Покупной образ заработать нельзя — только купить."""
    kind = rule[0]
    if kind == "coins":
        return 0, 1
    if kind == "level":
        return facts["level"], rule[1]
    if kind == "award":
        return facts["earned"].get(rule[1], 0), rule[2]
    source, goal = rule[2], rule[3]
    if source[0] == "stat":
        value = facts["stats"].get(source[1], {}).get(source[2], 0)
    elif source[0] == "counter":
        value = facts["counters"].get(source[1], 0)
    elif source[0] == "ending":
        value = int(f"end:{source[1]}:{source[2]}" in facts["earned"])
    else:
        value = facts["arcade"](source[1])
    return value, goal


def _award_titles() -> dict[str, tuple[str, str]]:
    import achievements  # здесь, а не наверху модуля: ачивки сами смотрят на уровень Толяна
    return {item[0]: (item[2], item[3]) for item in achievements.ACHIEVEMENTS}


def _skin_need(rule: tuple, titles: dict[str, tuple[str, str]]) -> str:
    """Что сделать, чтобы открыть образ — одной строкой для карточки."""
    if rule[0] == "level":
        return f"Дорастить до «{LEVELS[rule[1]][0]}»"
    if rule[0] == "award":
        emoji, title = titles.get(rule[1], ("🏅", rule[1]))
        return f"Ачивка {emoji} «{title}» на {'★' * rule[2]}"
    return rule[1]


def _perk_lines(perk: dict) -> list[dict]:
    """Бонус образа словами — для карточки. good: лучше, чем у обычного Толяна, или хуже."""
    lines = []

    def add(text: str, good: bool):
        lines.append({"text": text, "good": good})

    if "income" in perk:
        change = round((perk["income"] - 1) * 100)
        add(f"{'+' if change > 0 else '−'}{abs(change)}% к выручке", change > 0)
    if "cap" in perk:
        add(f"копит {perk['cap']} ч, а не {CAP_HOURS}", perk["cap"] > CAP_HOURS)
    if "feed_hours" in perk:
        add(f"сыт {perk['feed_hours']} ч, а не {FEED_HOURS}", perk["feed_hours"] > FEED_HOURS)
    if "feed_cost" in perk:
        add(f"плов за {perk['feed_cost']}, а не {FEED_COST}", perk["feed_cost"] < FEED_COST)
    if "hungry" in perk:
        add(f"голодный — {round(perk['hungry'] * 100)}% силы, а не {round(HUNGRY_RATE * 100)}%",
            perk["hungry"] > HUNGRY_RATE)
    if "find" in perk:
        add(f"находки {round(perk['find'] * 100)}%, а не {round(FIND_CHANCE * 100)}%", perk["find"] > FIND_CHANCE)
    if "find_mult" in perk:
        add(f"находки ×{perk['find_mult']:g}", perk["find_mult"] > 1)
    if "trouble" in perk:
        add("не грабят" if perk["trouble"] == 0 else
            f"грабят {round(perk['trouble'] * 100)}%, а не {round(TROUBLE_CHANCE * 100)}%",
            perk["trouble"] < TROUBLE_CHANCE)
    if "keep" in perk:
        add(f"после грабежа остаётся {round(perk['keep'] * 100)}%, а не {round(TROUBLE_SHARE * 100)}%",
            perk["keep"] > TROUBLE_SHARE)
    return lines


def _wardrobe(bum: dict) -> dict:
    chat_id, user_id = bum["chat_id"], bum["user_id"]
    owned = db.get_bum_skins(chat_id, user_id)
    facts = _skin_facts(chat_id, user_id, bum)
    titles = _award_titles()
    items = []
    for key, rule, title, emoji, about in SKINS:
        mine = owned.get(key)
        view = {"key": key, "kind": rule[0], "title": title, "emoji": emoji, "about": about,
                "perk": _perk_lines(SKIN_PERKS.get(key, {})),
                "owned": mine is not None, "worn": bool(mine and mine["worn"])}
        if rule[0] == "coins":
            view["price"] = rule[1]
        elif mine is None:
            value, goal = _skin_progress(rule, facts)
            view |= {"need": _skin_need(rule, titles), "value": min(value, goal), "goal": goal}
        items.append(view)
    return {
        "skin": next((key for key, mine in owned.items() if mine["worn"]), None),
        "skins": items,
        "kinds": [{"key": key, "emoji": emoji, "title": title} for key, emoji, title in SKIN_KINDS],
        "owned": len(owned),
        "total": len(SKINS),
    }


def claim_skins(chat_id: int, user_id: int) -> list[dict]:
    """Выдаёт образы, условия которых уже выполнены. Возвращает только что открытые."""
    bum = db.peek_bum(chat_id, user_id)
    if bum is None:                      # Толяна ещё не заводили — образы подождут его
        return []
    owned = db.get_bum_skins(chat_id, user_id)
    waiting = [skin for skin in SKINS if skin[0] not in owned and skin[1][0] != "coins"]
    if not waiting:
        return []
    facts = _skin_facts(chat_id, user_id, bum)
    fresh = []
    for key, rule, title, emoji, about in waiting:
        value, goal = _skin_progress(rule, facts)
        if value < goal or not db.add_bum_skin(chat_id, user_id, key, _now()):
            continue
        fresh.append({"key": key, "title": title, "emoji": emoji})
        if rule[0] == "deed":
            engine.add_feed(chat_id, f"{emoji} {texts.BUM_NAME} у {engine.player_name(user_id)} "
                                     f"получает образ «{title}»")
    return fresh


def _switch(bum: dict, now: int, key: str | None, story: str) -> dict:
    """Переодевание. Накопленное в старом образе забираем по старым правилам (с находками и ограблениями),
    иначе новый бонус посчитался бы задним числом, а переодеванием можно было бы уйти от ограбления."""
    chat_id, user_id = bum["chat_id"], bum["user_id"]
    paid, luck = _cash_out(bum, now, _perk(db.worn_bum_skin(chat_id, user_id)))
    db.wear_bum_skin(chat_id, user_id, key)
    if luck:
        story = f"{story}. {luck}"
    elif paid > 0:
        story = f"{story}. Выручка в кармане: +{money(paid)}"
    return _state(_bum(chat_id, user_id, now), story, wardrobe=True)


def wear(chat_id: int, user_id: int, key) -> dict:
    """Надеть открытый образ. Пустой ключ — снять и вернуть обычный вид по уровню."""
    engine.throttle(chat_id, user_id)
    now = _now()
    bum = _bum(chat_id, user_id, now)
    worn = db.worn_bum_skin(chat_id, user_id)
    if key is None or key == "":
        if worn is None:
            return _state(bum, "👕 Он и так в своём", wardrobe=True)
        return _switch(bum, now, None, "👕 Снова в своём")
    skin = _find_skin(key)
    if skin[0] not in db.get_bum_skins(chat_id, user_id):
        raise GameError("Этот образ ещё не открыт", 409)
    if skin[0] == worn:
        return _state(bum, f"{skin[3]} Он уже в этом образе", wardrobe=True)
    return _switch(bum, now, skin[0], f"{skin[3]} Образ «{skin[2]}» надет")


def buy(chat_id: int, user_id: int, key) -> dict:
    """Покупка образа за таджикоины. Купленный сразу надевается."""
    engine.throttle(chat_id, user_id)
    key, rule, title, emoji, about = _find_skin(key)
    if rule[0] != "coins":
        raise GameError("Этот образ не продаётся — его надо заслужить", 409)
    now = _now()
    bum = _bum(chat_id, user_id, now)
    bought = db.buy_bum_skin(chat_id, user_id, key, rule[1], now)
    if bought is None:
        raise GameError("Этот образ уже куплен", 409)
    if not bought:
        raise engine.not_enough_money(chat_id, user_id)
    if rule[1] >= SKIN_FEED_FROM:
        engine.add_feed(chat_id, f"{emoji} {texts.BUM_NAME} у {engine.player_name(user_id)} "
                                 f"теперь в образе «{title}»")
    return _switch(bum, now, key, f"{emoji} Куплено и надето: «{title}»")   # купил — сразу надел
