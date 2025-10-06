#!/bin/bash
# build-and-run.sh - Linux/macOS Docker build and run script

set -e

echo "🐳 Smart Shopping Assistant - Docker Build and Run"
echo "================================================"
echo

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "❌ Error: Docker is not installed"
    echo "Please install Docker and try again"
    exit 1
fi

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo "❌ Error: Docker is not running"
    echo "Please start Docker and try again"
    exit 1
fi

# Check for environment file
if [ ! -f ".env" ]; then
    echo "⚠️ Warning: .env file not found"
    echo "Please create .env file with your Azure OpenAI credentials"
    echo "See README.md for required environment variables"
    read -p "Press Enter to continue..."
fi

echo "🔨 Building Docker images..."
docker-compose build

echo "✅ Build completed successfully"
echo

echo "🚀 Starting services..."
docker-compose up -d

echo "✅ Services started successfully"
echo
echo "🌐 Application URLs:"
echo "  • Shopping Assistant: http://localhost:7860"
echo "  • Redis Insight:      http://localhost:8001"
echo
echo "📊 Check status:"
echo "  docker-compose ps"
echo
echo "📝 View logs:"
echo "  docker-compose logs -f"
echo
echo "🛑 Stop services:"
echo "  docker-compose down"
echo
echo "Press Enter to view logs..."
read
docker-compose logs -f
