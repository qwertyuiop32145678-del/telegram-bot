# Use explicit Python 3.11 tag
FROM python:3.11.16-slim

# Install system build deps needed for compiling C-extensions (asyncpg etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    python3-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements and install. Pre-install setuptools/wheel to help builds.
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel cython
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY . .

# Optional small check (can remove later)
RUN python --version && pip show asyncpg pydantic aiogram

CMD ["python", "bot.py"]
