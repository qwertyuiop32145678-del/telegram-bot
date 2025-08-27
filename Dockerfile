# Dockerfile — для Python 3.13 (пользователь просил 3.13)
FROM python:3.13-slim

# Небольшие системные пакеты (минимум)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Скопировать зависимости и установить
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# Скопировать код
COPY . .

# Запуск
CMD ["python", "bot.py"]
