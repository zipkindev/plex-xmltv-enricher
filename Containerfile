FROM python:3.13-slim-trixie

ARG APP_VERSION=0.4.0
LABEL org.opencontainers.image.title="plex-xmltv-enricher" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.description="Conservative provider-backed XMLTV resolver for Plex DVR"

RUN groupadd --system --gid 65532 enricher && useradd --system --uid 65532 --gid 65532 --home-dir /nonexistent --shell /usr/sbin/nologin enricher
WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir --disable-pip-version-check .
USER 65532:65532
EXPOSE 9193
ENTRYPOINT ["plex-xmltv-enricher"]
CMD ["serve", "--config", "/config/config.toml"]
