FROM public.ecr.aws/docker/library/python:3.12-alpine

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apk add --no-cache ca-certificates git nodejs npm \
    && npm install -g @anthropic-ai/claude-code

COPY pyproject.toml README.md ./
COPY db ./db
COPY src ./src

RUN python -m pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "cloud_agent_poc.web:app", "--host", "0.0.0.0", "--port", "8000"]
