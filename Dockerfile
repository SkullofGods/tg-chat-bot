# Explicit Python runtime for bothost: bot settings -> "Additional settings" ->
# enable the custom Dockerfile checkbox, then run a new deploy.
# Without it the host's auto-detection may start this project with Node.js.
# Keep this file ASCII-only: bothost docs warn that non-ASCII comments can break the build.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "bot.py"]
