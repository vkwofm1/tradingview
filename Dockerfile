FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY app/__init__.py app/__init__.py
RUN pip install --no-cache-dir .
COPY . .
EXPOSE 8509
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8509"]
