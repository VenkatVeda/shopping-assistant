# 🚀 Render.com Redis Deployment Guide

## 🆓 **IMPORTANT: Free Tier Limitation**

**Redis services are NOT available on Render's free tier.** You need to upgrade to the **Starter plan ($7/month)** to use Redis.

### **Free Tier Configuration:**
- ✅ Memory caching (works fine for testing)
- ❌ Redis caching (not available)
- ⚠️ Service sleeps after 15 minutes of inactivity
- ⚠️ Cache is lost when service sleeps

### **Starter Plan Benefits:**
- ✅ Redis caching (persistent)
- ✅ Always-on service (no sleep)
- ✅ Better performance
- ✅ Persistent sessions

## Problem Solved
Your application was falling back to memory cache instead of using Redis because:
1. `ENABLE_REDIS=false` in render.yaml
2. Redis connection was hardcoded to `localhost`
3. Missing environment variables for Redis connection

## ✅ Fixed Configuration

### 1. Updated render.yaml
```yaml
services:
  - type: web
    name: shopping-assistant
    env: docker
    dockerfilePath: ./Dockerfile
    dockerContext: ./
    plan: starter
    healthCheckPath: /health
    envVars:
      - key: ENABLE_REDIS
        value: true  # ✅ ENABLED
      - key: REDIS_HOST
        value: shopping-assistant-redis  # ✅ CONNECTS TO REDIS SERVICE
      - key: REDIS_PORT
        value: 6379
      # ... other env vars
    
  - type: redis
    name: shopping-assistant-redis  # ✅ REDIS SERVICE
    plan: starter
    maxmemoryPolicy: allkeys-lru
```

### 2. Updated main.py
- ✅ Reads `REDIS_HOST` and `REDIS_PORT` from environment variables
- ✅ Checks `ENABLE_REDIS` environment variable
- ✅ Better error handling and connection timeout
- ✅ Falls back gracefully to memory cache if Redis fails

## 🚀 Deployment Steps

### 1. Commit and Push Changes
```bash
git add .
git commit -m "Enable Redis on Render deployment"
git push origin main
```

### 2. Deploy on Render
1. Go to your Render dashboard
2. Your service will automatically redeploy with the new configuration
3. Monitor the deployment logs

### 3. Verify Redis is Working
Look for these log messages in your Render deployment:
```
✅ Redis cache connected to shopping-assistant-redis:6379
```

Instead of:
```
⚠️ Redis disabled via ENABLE_REDIS=false, using memory cache
```

## 📊 Redis Service Details

### Render Redis Service Features:
- **Plan**: `starter` (free tier)
- **Memory Policy**: `allkeys-lru` (removes least recently used items when memory is full)
- **Persistence**: Automatic backups
- **High Availability**: Built-in redundancy

### Connection Details:
- **Host**: `shopping-assistant-redis` (internal service name)
- **Port**: `6379` (standard Redis port)
- **Network**: Private network between services

## 🔍 Troubleshooting

### If Redis Still Not Working:

1. **Check Render Logs**:
   ```
   # In Render dashboard, check both services:
   # - shopping-assistant (web service)
   # - shopping-assistant-redis (redis service)
   ```

2. **Verify Environment Variables**:
   - `ENABLE_REDIS=true`
   - `REDIS_HOST=shopping-assistant-redis`
   - `REDIS_PORT=6379`

3. **Check Service Dependencies**:
   - Redis service should start before web service
   - Both services should be in the same region

4. **Test Redis Connection**:
   ```python
   # This is already implemented in main.py
   # Look for connection success/failure messages in logs
   ```

### Common Issues:

#### Service Not Starting
- **Issue**: Redis service fails to start
- **Solution**: Check Redis service logs in Render dashboard

#### Connection Timeout
- **Issue**: Web service can't connect to Redis
- **Solution**: Verify both services are in same region and Redis service name is correct

#### Memory Issues
- **Issue**: Redis runs out of memory
- **Solution**: Upgrade Redis plan or tune `maxmemoryPolicy`

## 💡 Performance Benefits

With Redis enabled, you'll see:
- ✅ **Faster response times** (cache hits)
- ✅ **Reduced API calls** to Azure OpenAI
- ✅ **Better user experience** (instant results for repeated queries)
- ✅ **Cost savings** (fewer API calls)

## 📈 Monitoring

### Cache Performance Metrics:
```python
# Your application automatically logs cache performance:
# "✅ Redis cache connected to shopping-assistant-redis:6379"
# "🎯 Cache hit: preference extraction"
# "🔄 Cache miss: calling Azure API..."
```

### Redis Metrics in Render:
- Memory usage
- Connection count
- Hit/miss ratios
- Response times

## 🎯 Success Verification

### Logs to Look For:
```
✅ Redis cache connected to shopping-assistant-redis:6379
🎯 Cache hit: preference extraction
🎯 Cache hit: vector search
   - Cache System: ✅ Redis
```

### NOT This (Old Behavior):
```
⚠️ Redis disabled via ENABLE_REDIS=false, using memory cache
   - Cache System: ⚠️ Memory
```

---

**Your Redis deployment is now properly configured! 🎉**

The application will automatically use Redis for caching, providing better performance and user experience.