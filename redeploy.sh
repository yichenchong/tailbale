docker build -t tailbale:latest .
docker build -t tailbale-edge:latest ./edge
docker rm -f tailbale
docker run -d \
  --name tailbale \
  --restart unless-stopped \
  -p 6780:8080 \
  -v /mnt/user/appdata/tailbale/data:/data \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e DATA_DIR=/data \
  -e DOCKER_SOCKET=unix:///var/run/docker.sock \
  -e PORT=8080 \
  tailbale:latest