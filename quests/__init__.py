"""Текстовые квесты для кнопки «выйти погулять».

Истории лежат по файлам-темам рядом (двор, базар, ночь, дорога, работа, праздники, природа
и один большой день). Внутри каждого файла — словарь QUESTS, а узлы записаны короткими кортежами,
чтобы историю было видно целиком, а не утонуть в скобках:

    "node":  (сцена, текст, ((вариант, куда), (вариант, куда), ...))   — развилка
    "end_x": (сцена, текст, название концовки, сколько таджикоинов)    — концовка

Здесь кортежи разворачиваются в словари, проверяются (все ходы ведут в существующие узлы, до
каждого узла можно дойти, сцена нарисована в приложении) и складываются в общий QUESTS. Сломанная
история не роняет бота: она просто не поедет в прогулки, а в логах будет видно, что с ней не так.

Картинку к узлу рисует приложение по ключу сцены — список SCENES должен совпадать с webapp/index.html.
"""

import logging
from importlib import import_module

logger = logging.getLogger(__name__)

# Сцены, которые умеет рисовать приложение (см. const SCENES в webapp/index.html)
SCENES = (
    "entrance", "yard", "street", "shop", "garages", "basement", "river", "wedding", "bus",
    "market", "station", "train", "kitchen", "roof", "park", "forest", "mountains", "cemetery",
    "bathhouse", "carwash", "shawarma", "teahouse", "police", "hospital", "school", "construction",
    "dump", "club", "field", "elevator", "balcony", "taxi", "lake", "snow", "bridge", "pitch",
)

MODULES = ("dvor", "bazar", "noch", "doroga", "rabota", "prazdnik", "dikoe", "den", "lor")

QUESTS: dict[str, dict] = {}
ENDINGS: dict[str, list[str]] = {}


def _node(raw: tuple) -> dict:
    scene, text = raw[0], raw[1]
    if len(raw) == 4:  # концовка: название для коллекции и сколько принёс домой
        return {"scene": scene, "text": text, "name": raw[2], "pay": raw[3]}
    return {"scene": scene, "text": text,
            "choices": [{"text": choice, "to": target} for choice, target in raw[2]]}


def _prepare(key: str, quest: dict) -> dict:
    """Разворачивает кортежи в узлы и проверяет историю целиком. Ошибка — ValueError."""
    nodes = {node_id: _node(raw) for node_id, raw in quest["nodes"].items()}
    start = quest["start"]
    if start not in nodes:
        raise ValueError(f"нет начального узла {start}")
    seen, queue = {start}, [start]
    while queue:  # обход от начала: заодно ловим узлы, до которых не дойти
        node = nodes[queue.pop()]
        if node["scene"] not in SCENES:
            raise ValueError(f"незнакомая сцена {node['scene']}")
        if not node["text"].strip():
            raise ValueError("пустой текст узла")
        if "pay" in node:
            continue
        if not node["choices"]:
            raise ValueError("развилка без вариантов")
        for choice in node["choices"]:
            if choice["to"] not in nodes:
                raise ValueError(f"ход в никуда: {choice['to']}")
            if choice["to"] not in seen:
                seen.add(choice["to"])
                queue.append(choice["to"])
    lost = set(nodes) - seen
    if lost:
        raise ValueError(f"до этих узлов не дойти: {', '.join(sorted(lost))}")
    if not any("pay" in node for node in nodes.values()):
        raise ValueError("история без концовок")
    return {"title": quest["title"], "emoji": quest.get("emoji", "📖"), "start": start, "nodes": nodes}


def _load():
    for name in MODULES:
        try:
            module = import_module(f"{__name__}.{name}")
        except Exception:
            logger.exception("Файл историй %s не загрузился", name)
            continue
        for key, quest in module.QUESTS.items():
            if key in QUESTS:
                logger.error("Квест %s объявлен дважды, вторая копия пропущена", key)
                continue
            try:
                prepared = _prepare(key, quest)
            except Exception as e:
                logger.error("Квест %s пропущен: %s", key, e)
                continue
            QUESTS[key] = prepared
            ENDINGS[key] = [node_id for node_id, node in prepared["nodes"].items() if "pay" in node]


_load()


def ending_name(quest_key: str, node_id: str) -> str:
    node = QUESTS.get(quest_key, {}).get("nodes", {}).get(node_id, {})
    return node.get("name", "Концовка")


def total_endings() -> int:
    return sum(len(ends) for ends in ENDINGS.values())
