# Используем официальный образ Python
FROM python:3.13-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем файл зависимостей
COPY requirements.txt .

# Обновляем pip и устанавливаем зависимости
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код бота
COPY . .

# Команда запуска бота
CMD ["python", "bot.py"]
