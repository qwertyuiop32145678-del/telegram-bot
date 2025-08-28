FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# обновляем pip и запрещаем сборку из исходников
RUN pip install --upgrade pip setuptools wheel

COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
