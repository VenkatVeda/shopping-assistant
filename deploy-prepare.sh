#!/bin/bash
# deploy-prepare.sh - Prepare Docker image for deployment

set -e

echo "🚀 Preparing Shopping Assistant for Deployment"
echo "=============================================="

# Configuration
IMAGE_NAME="shopping-assistant"
VERSION="latest"
REGISTRY_URL=""  # Set this to your registry URL

# Build production image
echo "📦 Building production Docker image..."
docker build -t $IMAGE_NAME:$VERSION .

# Tag for registry (update this with your registry)
if [ ! -z "$REGISTRY_URL" ]; then
    echo "🏷️ Tagging image for registry..."
    docker tag $IMAGE_NAME:$VERSION $REGISTRY_URL/$IMAGE_NAME:$VERSION
    
    echo "📤 Pushing to registry..."
    docker push $REGISTRY_URL/$IMAGE_NAME:$VERSION
    echo "✅ Image pushed to $REGISTRY_URL/$IMAGE_NAME:$VERSION"
else
    echo "⚠️ No registry URL set - image ready for local deployment"
fi

echo "✅ Deployment preparation complete!"
echo "📋 Image: $IMAGE_NAME:$VERSION"
echo "💾 Size: $(docker images $IMAGE_NAME:$VERSION --format 'table {{.Size}}')"