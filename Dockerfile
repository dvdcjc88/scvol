FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY eth_reversal_alert.py .

CMD ["python3", "-u", "eth_reversal_alert.py"]
