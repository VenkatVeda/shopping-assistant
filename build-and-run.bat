@echo off
REM build-and-run.bat - Windows Docker build and run script

echo 🐳 Smart Shopping Assistant - Docker Build and Run
echo ================================================
echo.

REM Check if Docker is running
docker version >nul 2>&1
if %errorlevel% neq 0 (
    echo ❌ Error: Docker is not running or not installed
    echo Please install Docker Desktop and ensure it's running
    pause
    exit /b 1
)

REM Check for environment file
if not exist ".env" (
    echo ⚠️ Warning: .env file not found
    echo Please create .env file with your Azure OpenAI credentials
    echo See README.md for required environment variables
    pause
)

echo 🔨 Building Docker images...
docker-compose build

if %errorlevel% neq 0 (
    echo ❌ Build failed
    pause
    exit /b 1
)

echo ✅ Build completed successfully
echo.

echo 🚀 Starting services...
docker-compose up -d

if %errorlevel% neq 0 (
    echo ❌ Failed to start services
    pause
    exit /b 1
)

echo ✅ Services started successfully
echo.
echo 🌐 Application URLs:
echo   • Shopping Assistant: http://localhost:7860
echo   • Redis Insight:      http://localhost:8001
echo.
echo 📊 Check status:
echo   docker-compose ps
echo.
echo 📝 View logs:
echo   docker-compose logs -f
echo.
echo 🛑 Stop services:
echo   docker-compose down
echo.
echo Press any key to view logs...
pause >nul
docker-compose logs -f
