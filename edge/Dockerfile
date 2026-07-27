FROM tailscale/tailscale:v1.98.9

ARG TARGETARCH=amd64
ARG TARGETVARIANT

# curl is a RUNTIME dependency: the orchestrator's HTTPS health probe execs
# `curl` inside this container via `docker exec` (see backend/app/health/probe.py,
# which relies on curl's exit-code semantics — busybox wget can't distinguish
# 4xx from connection failures). It must survive in the final image, so install
# it as a normal package rather than a build-only virtual dep.
RUN apk add --no-cache curl

# Install Caddy from the pinned official GitHub release (reproducible build)
ARG CADDY_VERSION=2.11.4
RUN apk add --no-cache --virtual .build-deps tar \
    && case "${TARGETARCH}/${TARGETVARIANT}" in \
        amd64/*) caddy_arch="amd64" ;; \
        arm64/*) caddy_arch="arm64" ;; \
        arm/v6) caddy_arch="armv6" ;; \
        arm/v7|arm/) caddy_arch="armv7" ;; \
        armv6/*) caddy_arch="armv6" ;; \
        armv7/*) caddy_arch="armv7" ;; \
        *) echo "Unsupported TARGETARCH=${TARGETARCH} TARGETVARIANT=${TARGETVARIANT}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_${caddy_arch}.tar.gz" \
       | tar -xz -C /usr/local/bin caddy \
    && chmod +x /usr/local/bin/caddy \
    && apk del .build-deps

# Create directories for certs and config
RUN mkdir -p /etc/caddy /certs /var/run/tailscale

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

LABEL tailbale.managed="true"

ENTRYPOINT ["/entrypoint.sh"]
