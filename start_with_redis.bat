@echo off
REM start_with_redis.bat - Windows batch script to start the shopping assistant with Redis caching

echo Starting Shopping Assistant with Redis Caching...
echo.

REM Check if Docker is running
docker version >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Docker is not running or not installed
    echo Please install Docker Desktop and ensure it's running
    echo See REDIS_CACHING_SETUP.md for instructions
    pause
    exit /b 1
)

REM Start Redis with Docker Compose
echo Starting Redis server...
docker-compose up -d redis

REM Wait a moment for Redis to start
timeout /t 3 /nobreak >nul

REM Check if Redis is responding
docker exec shopping-assistant-redis redis-cli ping >nul 2>&1
if %errorlevel% neq 0 (
    echo Error: Redis failed to start properly
    echo Try: docker-compose logs redis
    pause
    exit /b 1
)

echo Redis started successfully!
echo.

REM Install Python dependencies if needed
echo Checking Python dependencies...
pip show redis >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing Redis Python dependencies...
    pip install redis hiredis
)

echo.
echo Starting Shopping Assistant with caching enabled...
echo Redis Insight available at: http://localhost:8001
echo.

REM Start the cached application
python main_cached.py

REM Cleanup
echo.
echo Stopping Redis server...
docker-compose down

echo Done!
pause