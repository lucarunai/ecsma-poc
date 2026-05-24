FROM public.ecr.aws/docker/library/python:3.12-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/cloudagent
WORKDIR /app

RUN apk add --no-cache ca-certificates git nodejs npm \
    && npm install -g @anthropic-ai/claude-code

COPY pyproject.toml README.md ./
COPY db ./db
COPY src ./src

RUN python -m pip install --no-cache-dir . \
    && addgroup -S -g 10001 cloudagent \
    && adduser -S -D -u 10001 -G cloudagent -h /home/cloudagent cloudagent \
    && mkdir -p /home/cloudagent/.claude \
    && chown -R cloudagent:cloudagent /home/cloudagent /app \
    && chmod 1777 /tmp

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "cloud_agent_poc.web:app", "--host", "0.0.0.0", "--port", "8000"]
