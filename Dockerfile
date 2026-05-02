FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1

# Install uv
RUN pip install uv

COPY ./ /akamai-cloud-bot
WORKDIR /akamai-cloud-bot

# Install dependencies with uv
RUN uv sync --frozen

CMD ["uv", "run", "akamai_cloud_bot.py"]