FROM tailscale/tailscale:latest

ARG TARGETARCH=amd64
ARG TARGETVARIANT

# Install Caddy from official static binary
RUN apk add --no-cache curl \
    && case "${TARGETARCH}/${TARGETVARIANT}" in \
        amd64/*) caddy_url="https://caddyserver.com/api/download?os=linux&arch=amd64" ;; \
        arm64/*) caddy_url="https://caddyserver.com/api/download?os=linux&arch=arm64" ;; \
        arm/v6) caddy_url="https://caddyserver.com/api/download?os=linux&arch=arm&arm=6" ;; \
        arm/v7|arm/) caddy_url="https://caddyserver.com/api/download?os=linux&arch=arm&arm=7" ;; \
        armv6/*) caddy_url="https://caddyserver.com/api/download?os=linux&arch=arm&arm=6" ;; \
        armv7/*) caddy_url="https://caddyserver.com/api/download?os=linux&arch=arm&arm=7" ;; \
        *) echo "Unsupported TARGETARCH=${TARGETARCH} TARGETVARIANT=${TARGETVARIANT}" >&2; exit 1 ;; \
    esac \
    && curl -fsSL "$caddy_url" -o /usr/local/bin/caddy \
    && chmod +x /usr/local/bin/caddy

# Create directories for certs and config
RUN mkdir -p /etc/caddy /certs /var/run/tailscale

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

LABEL tailbale.managed="true"

ENTRYPOINT ["/entrypoint.sh"]
