# ====== 1. Базовый образ ======
FROM python:3.13-slim

# ====== 2. Рабочая директория ======
WORKDIR /app

# ====== 3. Копируем файлы проекта ======
COPY . .

# ====== 4. Установка зависимостей ======
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir aiogram==3.1.1 openpyxl

# ====== 5. Переменные окружения (для локального теста) ======
# ENV API_TOKEN=твой_токен
# ENV ADMIN_ID=твой_айди

# ====== 6. Команда запуска ======
CMD ["python", "bot.py"]
