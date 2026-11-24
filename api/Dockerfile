FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_APP=wsgi:app \
    RUFF_CACHE_DIR=/tmp/ruff-cache

# Tool caches go to /tmp, not the source folder: when the source is mounted from the host
# (local development, CI) the container's user may not be allowed to write there.

WORKDIR /app

# Set to requirements.txt for a production image without test tools.
ARG REQUIREMENTS=requirements-dev.txt
# constraints.txt pins every indirect dependency too (see README: "Updating dependencies").
COPY requirements.txt requirements-dev.txt constraints.txt ./
RUN pip install -r ${REQUIREMENTS} -c constraints.txt

COPY . .

RUN useradd --create-home --uid 1000 app && chown -R app:app /app
USER app

EXPOSE 5000
# No Gunicorn access log: the API writes its own JSON line per request, with the request id.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "wsgi:app"]
