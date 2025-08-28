FROM python:3.13-slim

WORKDIR /app

# нужно для сборки зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    libffi-dev \
    libssl-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# обновляем pip и ставим инструменты, чтобы не было "cargo build"
RUN pip install --upgrade pip setuptools wheel maturin

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
