FROM python:3.11-slim

RUN apt update && apt install -y sqlite3 procps && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bluetracker_seed.db /app/bluetracker_seed.db

COPY . /app

RUN mkdir -p /data

CMD ["python", "-m", "bot.main"]
