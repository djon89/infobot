FROM python:3.12-slim

# Node + Claude Code CLI — нужен только для провайдера claude_cli (личный режим владельца)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates ffmpeg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir .

RUN useradd -m bot
USER bot
CMD ["python", "-m", "app.bot.main"]
