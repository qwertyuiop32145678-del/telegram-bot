# Dockerfile
FROM python:3.11-slim

# Устанавливаем системные пакеты, нужные для сборки некоторых зависимостей (asyncpg, psycopg и т.д.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Копируем requirements и ставим зависимости
COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# Запуск
CMD ["python", "bot.py"]
