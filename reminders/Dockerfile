FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=Asia/Tokyo
RUN pip install --no-cache-dir "psycopg[binary]==3.2.*"
WORKDIR /app
COPY reminder.py .
CMD ["python", "reminder.py"]
