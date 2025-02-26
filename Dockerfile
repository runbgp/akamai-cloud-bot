FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1

COPY ./ /akamai-cloud-bot
WORKDIR /akamai-cloud-bot

RUN pip3 install -r requirements.txt

CMD ["python3", "bot.py"]