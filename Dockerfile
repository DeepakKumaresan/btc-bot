FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot code
COPY btc_audited.py .

# Run the bot
CMD ["python", "-u", "btc_audited.py"]
