FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=10000 \
    PATH="/opt/venv/bin:${PATH}" \
    BGUTIL_PROVIDER_URL="http://127.0.0.1:4416"

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ffmpeg git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -U pip \
    && pip install --no-cache-dir -r requirements.txt

# Build the matching BgUtils POT provider server.
RUN git clone --depth 1 --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/bgutil \
    && cd /opt/bgutil/server \
    && npm ci \
    && npx tsc \
    && npm cache clean --force

COPY . .
RUN mkdir -p /app/downloads /app/data \
    && chmod +x /app/start.sh

EXPOSE 10000 4416
CMD ["/app/start.sh"]
