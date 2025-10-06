#!/bin/bash
# start_with_redis.sh - Linux/WSL script to start the shopping assistant with Redis caching

echo "Starting Shopping Assistant with Redis Caching..."
echo

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed"
    echo "Please install Docker and ensure it's running"
    echo "See REDIS_CACHING_SETUP.md for instructions"
    exit 1
fi

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo "Error: Docker is not running"
    echo "Please start Docker and try again"
    exit 1
fi

# Start Redis with Docker Compose
echo "Starting Redis server..."
docker-compose up -d redis

# Wait for Redis to start
sleep 3

# Check if Redis is responding
if ! docker exec shopping-assistant-redis redis-cli ping &> /dev/null; then
    echo "Error: Redis failed to start properly"
    echo "Try: docker-compose logs redis"
    exit 1
fi

echo "Redis started successfully!"
echo

# Install Python dependencies if needed
echo "Checking Python dependencies..."
if ! pip show redis &> /dev/null; then
    echo "Installing Redis Python dependencies..."
    pip install redis hiredis
fi

echo
echo "Starting Shopping Assistant with caching enabled..."
echo "Redis Insight available at: http://localhost:8001"
echo

# Start the cached application
python main_cached.py

# Cleanup
echo
echo "Stopping Redis server..."
docker-compose down

echo "Done!"