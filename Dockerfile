# Dockerfile — использует проверенный Python 3.11
FROM python:3.11.16-slim

# Устанавливаем системные пакеты, нужные для сборки asyncpg и других расширений
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем список зависимостей и ставим их
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# (Опционально) проверим версии в процессе сборки — можно убрать, если не нужно
RUN python --version && pip show asyncpg pydantic aiogram

# Запуск
CMD ["python", "bot.py"]
