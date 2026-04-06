FROM tailscale/tailscale:latest

# Install Caddy from official static binary
RUN apk add --no-cache curl \
    && curl -fsSL "https://caddyserver.com/api/download?os=linux&arch=amd64" -o /usr/local/bin/caddy \
    && chmod +x /usr/local/bin/caddy \
    && apk del curl

# Create directories for certs and config
RUN mkdir -p /etc/caddy /certs /var/run/tailscale

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

LABEL tailbale.managed="true"

ENTRYPOINT ["/entrypoint.sh"]
