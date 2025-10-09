# 🚀 Render Deployment Guide

## Deploy to Render.com

### **Render.com (Recommended)**
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

**Why Render:**
- ✅ Zero configuration required
- ✅ Auto-deploys from Git
- ✅ Free tier available
- ✅ HTTPS included automatically
- ✅ Easy environment variable management
- ✅ Built-in health checks

### **Local Testing Before Deployment**
```bash
# Test locally first
docker-compose up -d
# Verify at http://localhost:7860

# Build production image
docker build -t shopping-assistant:latest .

# Test production image
docker run -p 7860:7860 \
  -e AZURE_OPENAI_API_KEY=your_key \
  -e AZURE_OPENAI_ENDPOINT=your_endpoint \
  -e AZURE_OPENAI_DEPLOYMENT_NAME=your_deployment \
  shopping-assistant:latest
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

### ✅ **Vector Database**
- ✅ Ensure `vector_db backups/chroma_db_numeric` contains your data
- ✅ Vector database has 3,409 items
- ✅ ChromaDB collection named "bags" exists

## 🔧 **Post-Deployment Steps**

1. **Test the deployment:**
   ```bash
   curl https://your-app-url.onrender.com/health
   ```

2. **Monitor the application:**
   - Check Render logs for errors
   - Verify vector database is working
   - Test chat functionality

3. **Set up monitoring:**
   - Use Render's built-in health checks
   - Monitor application logs
   - Set up uptime monitoring

## 🆘 **Troubleshooting**

### **Common Issues:**

#### **Deployment failing on Render:**
```bash
# Check build logs in Render dashboard
# Common fixes:
# - Verify environment variables are set
# - Check Dockerfile syntax
# - Ensure render.yaml is properly configured
```

#### **Vector database empty:**
```bash
# Verify the vector_db backups folder is committed to Git
# Check that the path in services/vector_service.py is correct
```

#### **Azure OpenAI errors:**
```bash
# Verify API key and endpoint in Render environment variables
# Check Azure OpenAI service is accessible
# Confirm deployment name is correct
```

---

**Deploy to Render.com for the easiest deployment experience!** 🚀