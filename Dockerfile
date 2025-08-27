# Используем стабильный Python 3.12
FROM python:3.12-slim

# Установим зависимости для сборки некоторых пакетов (asyncpg, psycopg)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Установим рабочую директорию
WORKDIR /app

# Скопируем requirements
COPY requirements.txt .

# Установим зависимости Python
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь проект
COPY . .

# Запуск бота
CMD ["python", "bot.py"]
