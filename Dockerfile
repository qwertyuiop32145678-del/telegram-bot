# Используем официальный образ Python 3.12
FROM python:3.12-slim

# Устанавливаем переменную окружения для отказа от буферизации вывода (чтобы лог был читаемым)
ENV PYTHONUNBUFFERED=1

# Обновляем pip и устанавливаем зависимости
RUN python -m pip install --upgrade pip

# Копируем файлы проекта
WORKDIR /app
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install -r requirements.txt

# Копируем весь код в контейнер
COPY . .

# Указываем команду запуска
CMD ["python", "main.py"]
