FROM infisical/cli:0.43.116 AS infisical

FROM python:3.13-alpine

WORKDIR /app
COPY --from=infisical /bin/infisical /usr/local/bin/infisical
COPY worker/worker.py worker/gcp.py worker/servicenow.py /app/
COPY worker/infisical-entrypoint.sh /usr/local/bin/infisical-entrypoint

RUN pip install --no-cache-dir "PyJWT[crypto]==2.15.0" "google-auth==2.58.1" "requests==2.32.3"

RUN addgroup -S aegis \
    && adduser -S -G aegis -u 10001 aegis \
    && chmod 0555 /usr/local/bin/infisical-entrypoint
USER aegis

ENTRYPOINT ["/usr/local/bin/infisical-entrypoint"]
CMD ["python", "/app/worker.py"]
