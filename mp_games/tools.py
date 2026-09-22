"""Общее для игр мультиплеера: лента стола и карты.

Карта — целое число 0..51, чтобы состояние легко ложилось в JSON:
    масть = card // 13  (0 ♠, 1 ♥, 2 ♦, 3 ♣),  достоинство = card % 13  (0 — двойка, 12 — туз).
Колода на 36 карт — та же нумерация, просто без младших достоинств.
"""

import random

RANKS = ("2", "3", "4", "5", "6", "7", "8", "9", "10", "В", "Д", "К", "Т")
SUITS = ("♠", "♥", "♦", "♣")
RED = (1, 2)                 # червы и бубны рисуем красным
SIX = 4                      # индекс шестёрки: с неё начинается колода на 36 карт


def feed(state: dict, line: str) -> dict:
    """Строчка в ленту стола — её видят все, кто за ним сидит."""
    lines = list(state.get("feed", []))
    lines.append(line)
    return state | {"feed": lines[-30:]}


def suit(card: int) -> int:
    return card // 13


def rank(card: int) -> int:
    return card % 13


def card_text(card: int) -> str:
    return f"{RANKS[rank(card)]}{SUITS[suit(card)]}"


def hand_text(cards) -> str:
    return " ".join(card_text(card) for card in cards)


def deck(small: bool = False) -> list[int]:
    """Перемешанная колода: 52 карты или 36, если small."""
    cards = [card for card in range(52) if not small or rank(card) >= SIX]
    random.shuffle(cards)
    return cards


def order(cards, trump: int | None = None) -> list[int]:
    """Карты в руку кладём по мастям, козырь — в конец."""
    return sorted(cards, key=lambda card: (suit(card) == trump, suit(card), rank(card)))
