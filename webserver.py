"""HTTP-сервер мини-приложения казино: отдаёт страницу и принимает ходы игроков.

Каждый запрос подписан Telegram (initData): по подписи сервер знает, кто играет,
и подделать чужой запрос нельзя. Беседа, в которой играют, приходит в start_param
ссылки на приложение (g1003706796442 → -1003706796442), без него — главная беседа.
"""

import json
import logging
import time
from datetime import datetime, timezone

from aiohttp import web
from aiogram.types import User
from aiogram.utils.web_app import safe_parse_webapp_init_data

import achievements
import casino_arcade as arcade
import casino_bank as bank
import casino_bum as bum
import casino_engine as engine
import casino_tables as tables
import casino_walk as walk
import casino_work as work
import donations
import members
from config import BASE_DIR, BOT_TOKEN, CASINO_APP_NAME, WEB_PORT, WEBAPP_DEV_USER_ID
from loader import db

logger = logging.getLogger(__name__)

APP_DIR = BASE_DIR / "webapp"
INIT_DATA_MAX_AGE_SECONDS = 24 * 3600
MEMBER_CACHE_SECONDS = 10 * 60

_member_checked: dict[tuple[int, int], float] = {}
_runner: web.AppRunner | None = None


def start_param_for(chat_id: int) -> str:
    """Беседа в параметре ссылки t.me/…?startapp=…"""
    return f"g{abs(chat_id)}"


def app_link(bot_username: str, chat_id: int) -> str:
    """Ссылка, которая открывает казино прямо из беседы. Без CASINO_APP_NAME — «главное» мини-приложение бота."""
    path = f"{bot_username}/{CASINO_APP_NAME}" if CASINO_APP_NAME else bot_username
    return f"https://t.me/{path}?startapp={start_param_for(chat_id)}"


def _chat_from_start_param(start_param: str | None) -> int:
    known = set(db.get_known_chats())
    if start_param and start_param.startswith("g") and start_param[1:].isdigit():
        chat_id = -int(start_param[1:])
        if chat_id in known:
            return chat_id
    main_chat = db.get_main_chat()
    if main_chat is None:
        raise engine.GameError("Бот ещё не знает ни одной беседы", 404)
    return main_chat


async def _player(request: web.Request) -> tuple[int, int]:
    """Кто играет и в какой беседе. Бросает GameError, если пускать нельзя."""
    init_data = request.headers.get("X-Init-Data", "")
    if not init_data and WEBAPP_DEV_USER_ID:
        user_id, start_param = int(request.headers.get("X-Dev-User") or WEBAPP_DEV_USER_ID), None
    else:
        if not init_data:
            # Приложение не получило данные входа: открыли не из Telegram или не загрузился его скрипт
            logger.warning("Казино без данных Telegram: %s", request.headers.get("User-Agent", "")[:200])
            raise engine.GameError("Казино не получило данные от Telegram. Открой его кнопкой в беседе "
                                   "или кнопкой «Казино» в меню бота", 401)
        try:
            data = safe_parse_webapp_init_data(BOT_TOKEN, init_data)
        except ValueError:
            raise engine.GameError("Telegram не подтвердил вход — закрой казино и открой заново", 401)
        if data.user is None:
            raise engine.GameError("Telegram не сказал, кто ты. Открой казино кнопкой в беседе", 401)
        if (datetime.now(timezone.utc) - data.auth_date).total_seconds() > INIT_DATA_MAX_AGE_SECONDS:
            raise engine.GameError("Сессия устарела — закрой и открой казино заново", 401)
        user = data.user
        members.remember_user(User(
            id=user.id, is_bot=bool(user.is_bot), first_name=user.first_name,
            last_name=user.last_name, username=user.username,
        ))
        user_id, start_param = user.id, data.start_param

    chat_id = _chat_from_start_param(start_param)
    key = (chat_id, user_id)
    if time.monotonic() - _member_checked.get(key, -MEMBER_CACHE_SECONDS) >= MEMBER_CACHE_SECONDS:
        if db.member_status(chat_id, user_id) is not True and await members.check_member(chat_id, user_id) is not True:
            raise engine.GameError("Казино только для участников беседы 🙅", 403)
        _member_checked[key] = time.monotonic()
    request["player"] = key
    return chat_id, user_id


async def _body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise engine.GameError("Кривой запрос")
    return data if isinstance(data, dict) else {}


def _json(data: dict, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, ensure_ascii=False))


@web.middleware
async def _errors(request: web.Request, handler):
    try:
        return await handler(request)
    except engine.GameError as e:
        return _json({"error": str(e)}, e.status)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Ошибка в API казино: %s", request.path)
        return _json({"error": "Что-то сломалось, попробуй ещё раз"}, 500)


AWARDS_POLL_SECONDS = 15   # на опросах состояния ачивки пересчитываем не чаще
_awards_checked: dict[tuple[int, int], float] = {}
_POLLING = ("/api/state", "/api/table", "/api/walk", "/api/arcade", "/api/bank", "/api/bum", "/api/work")


def _needs_check(request: web.Request, player: tuple[int, int]) -> bool:
    """Ходы проверяем всегда, а опросы состояния — раз в несколько секунд, чтобы не гонять базу."""
    if not request.path.endswith(_POLLING):
        return True
    now = time.monotonic()
    if now - _awards_checked.get(player, -AWARDS_POLL_SECONDS) < AWARDS_POLL_SECONDS:
        return False
    _awards_checked[player] = now
    return True


@web.middleware
async def _awards(request: web.Request, handler):
    response = await handler(request)
    player = request.get("player")
    if player is None or response.status != 200 or response.content_type != "application/json":
        return response
    if not _needs_check(request, player):
        return response
    try:
        unlocked = achievements.check(*player)
    except Exception:
        logger.exception("Не смог проверить ачивки")
        return response
    if not unlocked:
        return response
    data = json.loads(response.text)
    data["unlocked"] = unlocked
    data["balance"] = db.get_balance(*player)  # награды уже в кошельке
    return _json(data)


# ── API ───────────────────────────────────────────────────────────────────────


async def api_state(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    bank.pay_matured()
    return _json(engine.state(chat_id, user_id) | {
        "donate": donations.payment_info(),
        "bank": bank.summary(chat_id, user_id),
        "bum": bum.bum_state(chat_id, user_id),
        "work": work.summary(chat_id, user_id),
    })


async def api_bonus(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(engine.claim_bonus(chat_id, user_id))


async def api_slots(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(engine.play_slots(chat_id, user_id, body.get("bet")))


async def api_coin(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(engine.play_coin(chat_id, user_id, body.get("bet"), body.get("side")))


async def api_rr(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(engine.pull_trigger(chat_id, user_id))


async def api_blackjack(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    action = request.match_info["action"]
    if action == "start":
        body = await _body(request)
        return _json(engine.blackjack_start(chat_id, user_id, body.get("bet")))
    handlers = {"hit": engine.blackjack_hit, "stand": engine.blackjack_stand, "double": engine.blackjack_double}
    if action not in handlers:
        raise web.HTTPNotFound()
    return _json(handlers[action](chat_id, user_id))


async def api_donate(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(await donations.request(chat_id, user_id, body.get("amount"), body.get("message")))


# Общие столы: рулетка и скачки


async def api_table(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(tables.table_state(chat_id, user_id, body.get("game")))


async def api_table_bet(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(tables.place_bet(chat_id, user_id, body.get("game"), body.get("bet"), body.get("kind"), body.get("pick"),
                                  body.get("round")))


# Поиграть: бесконечные игры на рекорд


async def api_arcade(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(arcade.state(chat_id, user_id))


async def api_arcade_score(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(arcade.submit(chat_id, user_id, body.get("game"), body.get("score")))


# Халтура: мини-игры, где зарабатывают руками


async def api_work(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(work.state(chat_id, user_id))


async def api_work_start(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(work.start(chat_id, user_id, body.get("job")))


async def api_work_step(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(work.step(chat_id, user_id, await _body(request)))


async def api_work_finish(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(work.finish(chat_id, user_id, await _body(request)))


# Выйти погулять


async def api_walk(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(walk.state(chat_id, user_id))


async def api_walk_start(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(walk.start(chat_id, user_id))


async def api_walk_go(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(walk.go(chat_id, user_id, body.get("choice")))


async def api_achievements(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(achievements.state(chat_id, user_id))


# Бомж


async def api_bum(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(bum.bum_state(chat_id, user_id))


async def api_bum_action(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    actions = {"collect": bum.collect, "feed": bum.feed, "upgrade": bum.upgrade}
    action = request.match_info["action"]
    if action not in actions:
        raise web.HTTPNotFound()
    return _json(actions[action](chat_id, user_id))


# Банк


async def api_bank(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    return _json(bank.bank_state(chat_id, user_id))


async def api_bank_deposit(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(bank.deposit(chat_id, user_id, body.get("amount"), body.get("term")))


async def api_bank_withdraw(request: web.Request) -> web.Response:
    chat_id, user_id = await _player(request)
    body = await _body(request)
    return _json(bank.withdraw(chat_id, user_id, body.get("id")))


# ── Страница ──────────────────────────────────────────────────────────────────


def build_app() -> web.Application:
    app = web.Application(middlewares=[_errors, _awards])
    # Приложение — один файл: стили и скрипт внутри index.html (см. комментарий в нём)
    index_html = (APP_DIR / "index.html").read_text(encoding="utf-8")

    async def index(_: web.Request) -> web.Response:
        return web.Response(text=index_html, content_type="text/html", headers={"Cache-Control": "no-cache"})

    async def health(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def casino_redirect(_: web.Request) -> web.Response:
        raise web.HTTPFound("/casino/")

    app.router.add_get("/health", health)
    app.router.add_get("/casino", casino_redirect)
    for prefix in ("", "/casino"):  # приложение открывается и с корня домена, и с /casino/
        app.router.add_get(f"{prefix}/", index)
        app.router.add_post(f"{prefix}/api/state", api_state)
        app.router.add_post(f"{prefix}/api/bonus", api_bonus)
        app.router.add_post(f"{prefix}/api/slots", api_slots)
        app.router.add_post(f"{prefix}/api/coin", api_coin)
        app.router.add_post(f"{prefix}/api/rr", api_rr)
        app.router.add_post(f"{prefix}/api/blackjack/{{action}}", api_blackjack)
        app.router.add_post(f"{prefix}/api/donate", api_donate)
        app.router.add_post(f"{prefix}/api/table", api_table)
        app.router.add_post(f"{prefix}/api/table/bet", api_table_bet)
        app.router.add_post(f"{prefix}/api/arcade", api_arcade)
        app.router.add_post(f"{prefix}/api/arcade/score", api_arcade_score)
        app.router.add_post(f"{prefix}/api/work", api_work)
        app.router.add_post(f"{prefix}/api/work/start", api_work_start)
        app.router.add_post(f"{prefix}/api/work/step", api_work_step)
        app.router.add_post(f"{prefix}/api/work/finish", api_work_finish)
        app.router.add_post(f"{prefix}/api/achievements", api_achievements)
        app.router.add_post(f"{prefix}/api/walk", api_walk)
        app.router.add_post(f"{prefix}/api/walk/start", api_walk_start)
        app.router.add_post(f"{prefix}/api/walk/go", api_walk_go)
        app.router.add_post(f"{prefix}/api/bum", api_bum)
        app.router.add_post(f"{prefix}/api/bum/{{action}}", api_bum_action)
        app.router.add_post(f"{prefix}/api/bank", api_bank)
        app.router.add_post(f"{prefix}/api/bank/deposit", api_bank_deposit)
        app.router.add_post(f"{prefix}/api/bank/withdraw", api_bank_withdraw)
    return app


async def start():
    global _runner
    if WEB_PORT is None:
        logger.info("Порт не задан — мини-приложение казино не запущено")
        return
    if WEBAPP_DEV_USER_ID:
        logger.warning("WEBAPP_DEV_USER_ID задан: казино пускает без Telegram. Не делай так на хостинге!")
    _runner = web.AppRunner(build_app(), access_log=None)
    await _runner.setup()
    await web.TCPSite(_runner, host="0.0.0.0", port=WEB_PORT).start()
    logger.info("Мини-приложение казино слушает порт %s", WEB_PORT)


async def stop():
    if _runner is not None:
        await _runner.cleanup()
