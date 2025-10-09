# 🆓 Render Free Tier Deployment Guide

## 🎯 **Free Tier Limitations & Solutions**

### **❌ What's NOT Available on Free Tier:**
- Redis services (requires starter plan $7/month)
- Background workers
- Persistent disk storage
- Custom domains with SSL

### **✅ What WORKS on Free Tier:**
- Web services (with sleep after 15 minutes of inactivity)
- Environment variables
- GitHub auto-deployment
- HTTPS (on .onrender.com subdomain)
- Memory caching (fallback)

## 🔧 **Optimized Configuration for Free Tier**

### **Current render.yaml (Free Tier Optimized):**
```yaml
services:
  - type: web
    name: shopping-assistant
    env: docker
    dockerfilePath: ./Dockerfile
    dockerContext: ./
    plan: free  # Free tier
    healthCheckPath: /health
    envVars:
      - key: AZURE_OPENAI_ENDPOINT
        value: your_endpoint_here
      - key: AZURE_OPENAI_DEPLOYMENT_NAME  
        value: your_deployment_name
      - key: AZURE_OPENAI_API_KEY
        sync: false  # Set as secret in dashboard
      - key: ENABLE_REDIS
        value: false  # Not available on free tier
      - key: ENABLE_NER
        value: true
      - key: PORT
        value: 7860
    buildCommand: |
      echo "Building Shopping Assistant..."
    startCommand: python main.py prod
```

## 🚀 **Performance on Free Tier**

### **Memory Cache Performance:**
Your application will automatically use memory caching:
```
⚠️ Redis disabled via ENABLE_REDIS=false, using memory cache
   - Cache System: ⚠️ Memory
```

### **Expected Behavior:**
- ✅ **First query**: Normal response time (API calls)
- ✅ **Repeat queries**: Faster (memory cache hits)
- ⚠️ **After 15 min idle**: Service sleeps, cache lost
- ⚠️ **Cold start**: ~30-60 seconds to wake up

## 💡 **Free Tier Optimization Tips**

### **1. Enable Memory Cache Warming:**
```python
# Your application already does this
# Preloads common queries into memory
```

### **2. Optimize for Cold Starts:**
```python
# Keep vector database in memory
# Pre-initialize frequently used services
```

### **3. Session Management:**
```python
# Sessions work fine with memory storage
# Will reset after service sleep
```

## 🆙 **Upgrade Path: Starter Plan Benefits**

### **For $7/month you get:**
- ✅ **Redis caching** (persistent across restarts)
- ✅ **No sleep** (always-on service)
- ✅ **Better performance** (no cold starts)
- ✅ **Persistent sessions**
- ✅ **Background jobs**

### **Redis Configuration for Starter Plan:**
```yaml
# Add this to render.yaml when you upgrade
services:
  - type: redis
    name: shopping-assistant-redis
    plan: starter
    maxmemoryPolicy: allkeys-lru

# And in web service:
envVars:
  - key: ENABLE_REDIS
    value: true
  - key: REDIS_HOST
    value: shopping-assistant-redis
  - key: REDIS_PORT
    value: 6379
```

## 🔍 **Free Tier Monitoring**

### **What to Watch:**
```bash
# In Render dashboard logs:
✅ "⚠️ Redis disabled via ENABLE_REDIS=false, using memory cache"
✅ "Application starting on free tier"
✅ "Memory cache initialized"
⚠️ "Service going to sleep after 15 minutes"
```

### **Performance Metrics:**
- **First request**: 2-5 seconds (warm)
- **Cached requests**: 200-500ms
- **Cold start**: 30-60 seconds
- **Memory limit**: 512MB

## 🎯 **Best Practices for Free Tier**

### **1. Optimize Memory Usage:**
```python
# Your app automatically:
# - Uses efficient memory cache
# - Cleans up expired sessions
# - Manages vector database efficiently
```

### **2. Handle Cold Starts:**
```python
# Your health check endpoint helps:
# - Renders knows service is ready
# - Faster recovery from sleep
```

### **3. User Experience:**
```python
# Show loading states for:
# - First query after cold start
# - Complex searches
```

## 📊 **Free vs Paid Comparison**

| Feature | Free Tier | Starter ($7/mo) |
|---------|-----------|-----------------|
| **Caching** | Memory only | Redis persistent |
| **Uptime** | Sleeps after 15min | Always on |
| **Cold Start** | 30-60 seconds | None |
| **Sessions** | Lost on sleep | Persistent |
| **Performance** | Good when warm | Excellent |
| **API Costs** | Higher (no persistence) | Lower (Redis cache) |

## 🚀 **Deployment Commands**

```bash
# Deploy to free tier
git add .
git commit -m "Deploy to Render free tier"
git push origin main

# Monitor deployment
# Check Render dashboard for build logs
```

## 🆘 **Troubleshooting Free Tier**

### **Service Won't Start:**
- Check memory usage (512MB limit)
- Verify environment variables
- Check build logs in dashboard

### **Slow Performance:**
- Expected on cold start
- Improve after first few requests
- Consider upgrading to starter

### **Cache Not Working:**
- Memory cache should work fine
- Cache lost after service sleep
- Normal behavior on free tier

---

**Your free tier deployment will work great for testing and light usage! 🎉**

Upgrade to starter plan when you need persistent Redis caching and always-on performance.