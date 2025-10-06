# 🚀 Docker Deployment Guide

## Choose Your Deployment Platform

### 🔥 **Quick Deploy (Recommended for Beginners)**

#### **1. Render.com (Easiest)**
```bash
# 1. Push code to GitHub
git add .
git commit -m "Ready for deployment"
git push origin main

# 2. Connect to Render.com
# - Go to https://render.com
# - Connect your GitHub repository
# - Render will auto-detect the render.yaml file
# - Add your AZURE_OPENAI_API_KEY as a secret
# - Deploy!
```

**Pros:** 
- ✅ Zero configuration
- ✅ Auto-deploys from Git
- ✅ Free tier available
- ✅ HTTPS included

#### **2. Railway (Docker-focused)**
```bash
# 1. Install Railway CLI
npm install -g @railway/cli

# 2. Login and deploy
railway login
railway link
railway up

# 3. Set environment variables
railway variables set AZURE_OPENAI_API_KEY=your_key_here
railway variables set AZURE_OPENAI_ENDPOINT=your_endpoint
```

**Pros:**
- ✅ Docker-native
- ✅ Simple CLI
- ✅ Good for Docker images

### 🏢 **Enterprise Deploy (Production-ready)**

#### **3. Azure Container Instances**
```bash
# 1. Build and push to Azure Container Registry
az acr build --registry yourregistry --image shopping-assistant:latest .

# 2. Deploy to ACI
az container create \
  --resource-group your-rg \
  --name shopping-assistant \
  --image yourregistry.azurecr.io/shopping-assistant:latest \
  --cpu 1 \
  --memory 2 \
  --ports 7860 \
  --environment-variables \
    AZURE_OPENAI_ENDPOINT=your_endpoint \
    AZURE_OPENAI_DEPLOYMENT_NAME=your_deployment \
  --secure-environment-variables \
    AZURE_OPENAI_API_KEY=your_key
```

#### **4. AWS ECS/Fargate**
```bash
# 1. Build and push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin your-account.dkr.ecr.us-east-1.amazonaws.com
docker tag shopping-assistant:latest your-account.dkr.ecr.us-east-1.amazonaws.com/shopping-assistant:latest
docker push your-account.dkr.ecr.us-east-1.amazonaws.com/shopping-assistant:latest

# 2. Register task definition
aws ecs register-task-definition --cli-input-json file://aws-ecs-task.json

# 3. Create service
aws ecs create-service \
  --cluster your-cluster \
  --service-name shopping-assistant \
  --task-definition shopping-assistant:1 \
  --desired-count 1 \
  --launch-type FARGATE
```

#### **5. Google Cloud Run**
```bash
# 1. Build and push to Container Registry
gcloud builds submit --tag gcr.io/your-project/shopping-assistant

# 2. Deploy to Cloud Run
gcloud run deploy shopping-assistant \
  --image gcr.io/your-project/shopping-assistant \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars ENABLE_NER=true \
  --set-env-vars AZURE_OPENAI_ENDPOINT=your_endpoint
```

### 🏠 **Self-Hosted Options**

#### **6. Local Production Server**
```bash
# Using production docker-compose
docker-compose -f docker-compose.prod.yml up -d

# With custom domain (using Nginx)
docker-compose -f docker-compose.prod.yml --profile with-nginx up -d
```

#### **7. VPS/Dedicated Server**
```bash
# 1. Install Docker on your server
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# 2. Copy your project
scp -r shopping_assistant user@your-server:/opt/

# 3. Run on server
ssh user@your-server
cd /opt/shopping_assistant
docker-compose -f docker-compose.prod.yml up -d
```

## 📋 Pre-Deployment Checklist

### ✅ **Required Environment Variables**
```env
AZURE_OPENAI_API_KEY=your_api_key_here          # REQUIRED
AZURE_OPENAI_ENDPOINT=your_endpoint_here        # REQUIRED
AZURE_OPENAI_DEPLOYMENT_NAME=your_deployment    # REQUIRED
AZURE_OPENAI_API_VERSION=2024-02-15-preview    # Optional
ENABLE_NER=true                                  # Optional
ENABLE_REDIS=true                               # Optional
```

### ✅ **Image Preparation**
```bash
# 1. Test locally first
docker-compose up -d
# Verify at http://localhost:7860

# 2. Build production image
docker build -t shopping-assistant:latest .

# 3. Test production image
docker run -p 7860:7860 \
  -e AZURE_OPENAI_API_KEY=your_key \
  -e AZURE_OPENAI_ENDPOINT=your_endpoint \
  -e AZURE_OPENAI_DEPLOYMENT_NAME=your_deployment \
  shopping-assistant:latest
```

### ✅ **Vector Database**
- ✅ Ensure `vector_db backups/chroma_db_numeric` contains your data
- ✅ Vector database has 3,409 items
- ✅ ChromaDB collection named "bags" exists

## 🎯 **Recommended Deployment Path**

### **For Testing/Development:**
1. **Render.com** - Easiest to start with
2. **Railway** - Good Docker experience

### **For Production:**
1. **Azure Container Instances** - If already using Azure
2. **Google Cloud Run** - For serverless scaling
3. **AWS ECS** - For full AWS ecosystem

### **For Cost-Effective:**
1. **VPS with Docker** - Most control, lowest cost
2. **Render.com free tier** - Good for demos

## 🔧 **Post-Deployment Steps**

1. **Test the deployment:**
   ```bash
   curl https://your-app-url.com/health
   ```

2. **Monitor the application:**
   - Check logs for errors
   - Verify vector database is working
   - Test chat functionality

3. **Set up monitoring:**
   - Health check endpoints
   - Log aggregation
   - Performance monitoring

## 🆘 **Troubleshooting**

### **Common Issues:**

#### **Container not starting:**
```bash
# Check logs
docker logs container-name

# Common fixes:
# - Verify environment variables
# - Check image build logs
# - Ensure ports are available
```

#### **Vector database empty:**
```bash
# Verify mount path
docker exec container-name ls -la "/app/vector_db backups/"

# Check ChromaDB data
docker exec container-name python -c "import chromadb; print(chromadb.PersistentClient(path='/app/vector_db backups/chroma_db_numeric').get_collection('bags').count())"
```

#### **Azure OpenAI errors:**
```bash
# Test API connectivity
docker exec container-name python -c "from services.azure_service import AzureService; print(AzureService().is_available())"
```

---

**Choose your deployment method and follow the specific instructions above!** 🚀