# Containerised India Procurement Watch — run it on any free host
# (Fly.io, Render, Railway, a free VM, your own box) and front it with a
# Cloudflare Tunnel for a free public URL. Build context = repo root:
#
#   docker build -f deploy/cloudflare/Dockerfile -t ipw .
#   docker run -p 5055:5055 -v "$PWD:/data:ro" -e DB_DIR=/data ipw
#
# The multi-GB SQLite files are NOT baked into the image — mount them at runtime
# (here the repo dir is mounted read-only at /data). summary.db / network.db are
# read from the working dir by default; mount or copy them next to app.py.
FROM python:3.12-slim

WORKDIR /app

# system deps kept minimal (sqlite3 ships with python)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

# app code (frontend + python). Data DBs are mounted at runtime, see header.
COPY . .

EXPOSE 5055
# threaded for the app's thread-local SQLite connections
CMD ["gunicorn", "-w", "2", "--threads", "8", "--timeout", "120", \
     "-b", "0.0.0.0:5055", "app:app"]
