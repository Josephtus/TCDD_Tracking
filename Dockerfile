FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Tarayıcılar ve sistem bağımlılıkları bu resmi imajda zaten var, '--with-deps' e gerek yok.

COPY . .

CMD ["python", "app/main.py"]