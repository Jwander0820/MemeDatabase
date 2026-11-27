# 基底映像以標籤＋多架構 digest 固定；安全更新須明確更新 digest（見 docs/backend-refactor.md）。
FROM python:3.15.1-slim-bookworm@sha256:575f8bc12ec26542e0678bc6b60e385218248019d433157ec327264a78c3a853 AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
COPY requirements-locks/public-linux.txt ./requirements-locks/
COPY LICENSE ./
# 先讀含雜湊的鎖檔，再讀來源宣告，避免 pip 25 將重複需求視為沒有雜湊。
RUN pip install --require-hashes --only-binary=:all: -r requirements-locks/public-linux.txt -r requirements.txt && useradd --uid 10001 --create-home app
RUN mkdir -p src/anime_memes && touch src/__init__.py src/anime_memes/__init__.py
COPY src/anime_memes/settings.py src/anime_memes/text.py src/anime_memes/search.py src/anime_memes/urls.py src/anime_memes/public_data.py src/anime_memes/releases.py src/anime_memes/release_bundle.py src/anime_memes/public_web.py src/anime_memes/runtime.py ./src/anime_memes/

FROM base AS public
# 部署版號：/api/v1/meta（未帶 version 時）會回傳，用來確認上線的是哪個 commit。
ARG BUILD_REVISION=
ARG BUILD_TIME=
ENV MEMEDB_BUILD_REVISION=$BUILD_REVISION MEMEDB_BUILD_TIME=$BUILD_TIME
USER 10001
CMD ["python", "-m", "src.anime_memes.runtime", "public", "--host", "0.0.0.0"]

FROM base AS private
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client && rm -rf /var/lib/apt/lists/*
COPY requirements-admin.txt ./
COPY requirements-locks/admin-linux.txt ./requirements-locks/
RUN pip install --require-hashes --only-binary=:all: -r requirements-locks/admin-linux.txt -r requirements-admin.txt
COPY src/anime_memes ./src/anime_memes
COPY config ./config
RUN mkdir -p /data /releases && chown app:app /data /releases
USER 10001
CMD ["python", "-m", "src.anime_memes.runtime", "private", "--host", "0.0.0.0"]

FROM base AS mcp
COPY requirements-mcp.txt ./
COPY requirements-locks/mcp-linux.txt ./requirements-locks/
RUN pip install --require-hashes --only-binary=:all: -r requirements-locks/mcp-linux.txt -r requirements-mcp.txt
COPY src/anime_memes/queries.py src/anime_memes/private_mcp.py ./src/anime_memes/
USER 10001
CMD ["python", "-m", "src.anime_memes.private_mcp"]
