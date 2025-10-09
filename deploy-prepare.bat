@echo off
REM deploy-prepare.bat - Prepare Docker image for deployment (Windows)

echo 🚀 Preparing Shopping Assistant for Deployment
echo ==============================================

REM Configuration
set IMAGE_NAME=shopping-assistant
set VERSION=latest
set REGISTRY_URL=

REM Build production image
echo 📦 Building production Docker image...
docker build -t %IMAGE_NAME%:%VERSION% .

if %errorlevel% neq 0 (
    echo ❌ Docker build failed
    pause
    exit /b 1
)

REM Tag for registry (update with your registry)
if not "%REGISTRY_URL%"=="" (
    echo 🏷️ Tagging image for registry...
    docker tag %IMAGE_NAME%:%VERSION% %REGISTRY_URL%/%IMAGE_NAME%:%VERSION%
    
    echo 📤 Pushing to registry...
    docker push %REGISTRY_URL%/%IMAGE_NAME%:%VERSION%
    echo ✅ Image pushed to %REGISTRY_URL%/%IMAGE_NAME%:%VERSION%
) else (
    echo ⚠️ No registry URL set - image ready for local deployment
)

echo ✅ Deployment preparation complete!
echo 📋 Image: %IMAGE_NAME%:%VERSION%

REM Show image size
docker images %IMAGE_NAME%:%VERSION% --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}"

pause