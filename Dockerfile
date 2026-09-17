# Запасной вариант для bothost: «Дополнительные настройки» → «Использовать свой Dockerfile».
# Нужен, если хостинг не угадал, что бот на Python, и пытается запускать его через Node.js.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "bot.py"]
