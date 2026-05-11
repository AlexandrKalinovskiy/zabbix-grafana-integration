#!/bin/bash
# install-docker.sh — Install Docker Engine + Compose on Debian/Ubuntu

set -euo pipefail

echo "==> Updating package index..."
sudo apt-get update -qq

echo "==> Installing prerequisites..."
sudo apt-get install -y -qq \
    ca-certificates \
    curl \
    gnupg \
    lsb-release

echo "==> Adding Docker's official GPG key..."
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
    -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "==> Adding Docker repository..."
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

echo "==> Updating package index (with Docker repo)..."
sudo apt-get update -qq

echo "==> Installing Docker Engine, CLI, Containerd and Compose plugin..."
sudo apt-get install -y \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin

echo "==> Adding current user to docker group..."
sudo usermod -aG docker "$USER"

echo "==> Enabling and starting Docker service..."
sudo systemctl enable docker
sudo systemctl start docker

echo ""
echo "✅ Docker installation complete!"
docker --version
docker compose version
