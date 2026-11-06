FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/

RUN mkdir -p /data

ENV DB_PATH=/data/church.db

# The commit this image was built from, reported at /api/info. Last, so a new commit
# doesn't invalidate the layers above.
ARG GIT_SHA=
ENV GIT_SHA=$GIT_SHA

EXPOSE 8000

# No access log: request paths name service ids, and nginx keeps a redacted log instead.
# Chapel viewers only ever send keep-alives, so their messages are kept small.
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--ws-max-size", "4096"]
