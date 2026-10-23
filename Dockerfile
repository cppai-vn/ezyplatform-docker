ARG EZ_VERSION=latest
ARG EZYARTICLE_VERSION=latest
# Extract the platform in a build-only stage; Python is not shipped in runtime.
FROM python:3.14-alpine AS platform
ARG EZ_VERSION
ARG EZYARTICLE_VERSION
WORKDIR /download
ADD https://ezyplatform.com/api/v1/platforms/${EZ_VERSION}/download ezyplatform.zip
ADD https://ezyplatform.com/api/v1/released-projects/ezyarticle/${EZYARTICLE_VERSION}/download?platformVersion=${EZ_VERSION} ezyarticle.zip
COPY preinstall-modules.py /download/
RUN python -m zipfile -e ezyplatform.zip /output && \
    python -I preinstall-modules.py /output/ezyplatform ezyarticle.zip

# Keep Java 11 without relying on retired Debian 11 security package downloads.
FROM eclipse-temurin:11-jre
ARG EZ_VERSION
LABEL org.opencontainers.image.title="EzyPlatform" \
    org.opencontainers.image.version="${EZ_VERSION}"
WORKDIR /app/ezyplatform
ENV TZ=Etc/UTC \
    DB_HOST=localhost \
    DB_PORT=3306 \
    DB_NAME=blog \
    DB_TABLES_CREATE_MANUALLY=false \
    AUTO_START_WEB=false

COPY --from=platform /output/ezyplatform/ /app/ezyplatform/
COPY --chmod=755 entrypoint.sh /app/entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=15s --start-period=180s --retries=3 \
    CMD curl -fsS --max-time 5 "${ADMIN_HEALTHCHECK_URL:-http://127.0.0.1:9090/}" -o /dev/null && \
        ([ "$AUTO_START_WEB" != "true" ] || curl -fsS --max-time 5 "${WEB_HEALTHCHECK_URL:-http://127.0.0.1:8080/}" -o /dev/null)

EXPOSE 9090 8080 3005 2208 2812/udp
