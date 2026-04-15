FROM python:3.12-slim

WORKDIR /app

# Increase pip timeout to handle slow network inside Docker
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=120 -r requirements.txt

# Copy application source
COPY models.py transactions.py main.py ./

# DATABASE_URL can be overridden via docker-compose environment or -e flag
ENV DATABASE_URL=postgresql://store_user:store_pass@db:5432/online_store

CMD ["python", "main.py"]
