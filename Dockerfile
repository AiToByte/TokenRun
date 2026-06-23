# TokenRun Backend
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY core/ core/
COPY gateway/ gateway/
COPY api/ api/
COPY main.py .
COPY runfiles/ runfiles/

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
