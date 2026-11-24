FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt && useradd --uid 10001 --create-home app
RUN mkdir -p src/anime_memes && touch src/__init__.py src/anime_memes/__init__.py
COPY src/anime_memes/text.py src/anime_memes/search.py src/anime_memes/urls.py src/anime_memes/public_data.py src/anime_memes/releases.py src/anime_memes/public_web.py src/anime_memes/runtime.py ./src/anime_memes/

FROM base AS public
USER 10001
CMD ["python", "-m", "src.anime_memes.runtime", "public", "--host", "0.0.0.0"]

FROM base AS private
COPY requirements-admin.txt ./
RUN pip install -r requirements-admin.txt
COPY src/anime_memes ./src/anime_memes
COPY config ./config
RUN mkdir -p /data /releases && chown app:app /data /releases
USER 10001
CMD ["python", "-m", "src.anime_memes.runtime", "private", "--host", "0.0.0.0"]

FROM base AS mcp
COPY requirements-mcp.txt ./
RUN pip install -r requirements-mcp.txt
COPY src/anime_memes/queries.py src/anime_memes/private_mcp.py ./src/anime_memes/
USER 10001
CMD ["python", "-m", "src.anime_memes.private_mcp"]
