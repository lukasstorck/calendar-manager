FROM python:3.14.6-slim

# install curl
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home appuser
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000
USER appuser

CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--log-level", "$LOG_LEVEL"]
# CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--log-level", "$LOG_LEVEL"]   # for production
