#!/bin/bash

set -e

echo "==> Copying deployment files..."
cp ./linux_based_deployment/* .

echo "==> Creating persistent directories..."
sudo mkdir -p /app/vectra/sqlite_data /app/vectra/chroma_data

echo "==> Setting permissions..."
sudo chmod -R 775 /app/vectra/sqlite_data /app/vectra/chroma_data

echo "==> Cleaning old data..."
sudo rm -rf /app/vectra/sqlite_data/* /app/vectra/chroma_data/*

echo "==> Stopping existing containers..."
docker compose down --remove-orphans || true

echo "==> Building and starting containers..."
docker compose up --build -d

echo "==> Deployment complete!"