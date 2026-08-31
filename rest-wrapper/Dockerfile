FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md LICENSE.md ./
COPY src ./src
COPY rest-wrapper/requirements.txt ./rest-wrapper/requirements.txt

RUN python -m pip install --no-cache-dir -r rest-wrapper/requirements.txt \
    && python -m pip install --no-cache-dir . \
    && useradd --create-home --uid 10001 appuser

COPY rest-wrapper/app.py ./rest-wrapper/app.py

WORKDIR /app/rest-wrapper

USER 10001

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
