FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir "mcp==1.30.0" "google-auth==2.58.1" "requests==2.32.3"
COPY worker/gcp.py mcp_gateway/server.py /app/
RUN useradd --system --uid 10001 aegis
USER aegis
ENV PYTHONUNBUFFERED=1
CMD ["python", "/app/server.py"]
