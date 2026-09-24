from aiogram import Router

from . import casino, chat, debug, donations, family, fun, profile, service, shop, stats, tajik_day


def build_router() -> Router:
    router = Router(name="root")
    # Порядок важен: chat ловит все остальные сообщения, поэтому он последний
    router.include_routers(
        debug.router,
        donations.router,
        service.router,
        profile.router,
        family.router,
        fun.router,
        casino.router,
        shop.router,
        tajik_day.router,
        stats.router,
        chat.router,
    )
    return router
