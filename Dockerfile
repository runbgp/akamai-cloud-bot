FROM python:3.14-slim

LABEL org.opencontainers.image.source=https://github.com/runbgp/akamai-cloud-bot

ENV PYTHONUNBUFFERED=1

# Install uv
RUN pip install uv

COPY ./ /akamai-cloud-bot
WORKDIR /akamai-cloud-bot

# Install dependencies with uv
RUN uv sync --frozen

CMD ["uv", "run", "akamai_cloud_bot.py"]