FROM python:3.11-slim

WORKDIR /app
COPY scripts/mock-asset-api.py .

EXPOSE 8000
CMD ["python", "-u", "mock-asset-api.py"]
