# tg-chat-bot

Telegram бот для групповых бесед с анкетами, браками и весёлыми командами.

## Команды

| Команда | Описание |
|---|---|
| `/info` (реплай) | Показать анкету пользователя |
| `/info @username` | Показать анкету по юзернейму |
| `/анкета` | Заполнить/обновить свою анкету |
| `/жениться` (реплай) | Заключить брак |
| `/выйтизамуж` (реплай) | Заключить брак (алиас) |
| `/развод` (реплай) | Расторгнуть брак |
| `/families` | Показать все браки в беседе |
| `/оргия` | Запустить опрос (1 раз в сутки) |

## Быстрый старт

См. раздел **Как запустить** ниже.

### 1. Создай бота
1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Отправь `/newbot`, задай имя и юзернейм
3. Скопируй токен

### 2. Включи необходимые настройки бота
В @BotFather:
- `/setprivacy` → выбери своего бота → `Disable`  
  _(чтобы бот читал все сообщения в группе)_

### 3. Клонируй репозиторий
```bash
git clone https://github.com/SkullofGods/tg-chat-bot.git
cd tg-chat-bot
```

### 4. Настрой окружение
```bash
cp .env.example .env
# Открой .env и вставь токен бота
```

### 5. Установи зависимости
```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 6. Запусти бота
```bash
python bot.py
```

### 7. Добавь бота в беседу
1. Добавь бота в групповой чат
2. Сделай его **администратором** (нужны права: читать сообщения, удалять сообщения, управлять опросами)
3. В настройках группы: **Group type → Supergroup** (если ещё не так)

> **Важно:** Для получения событий о новых участниках группа должна быть супергруппой.

## Структура проекта

```
tg-chat-bot/
├── bot.py          # Основная логика бота
├── db.py           # Работа с SQLite базой данных
├── config.py       # Конфигурация из .env
├── requirements.txt
├── .env.example    # Шаблон переменных окружения
└── .gitignore
```

## Запуск через Docker (опционально)

```dockerfile
# Dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "bot.py"]
```

```bash
docker build -t tg-chat-bot .
docker run -d --env-file .env --name tg-chat-bot tg-chat-bot
```
