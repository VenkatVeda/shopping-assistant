# 🚀 Upstash Redis Setup Guide

## 🎯 **Why Upstash for Your Shopping Assistant**

✅ **Free Tier**: 10,000 commands per day  
✅ **Serverless**: Perfect for apps that sleep  
✅ **Global**: Low latency worldwide  
✅ **REST API**: Works anywhere  
✅ **No Infrastructure**: Fully managed  

## 📋 **Step-by-Step Setup**

### **Step 1: Create Upstash Account**
1. Go to https://upstash.com/
2. Sign up with GitHub/Google (recommended)
3. Verify your email

### **Step 2: Create Redis Database**
1. Click **"Create Database"**
2. **Database Name**: `shopping-assistant-cache`
3. **Region**: Choose closest to your users (or `us-east-1` for Render)
4. **Type**: Regional (free tier)
5. Click **"Create"**

### **Step 3: Get Connection Details**
After creation, you'll see:
```
Endpoint: global-xyz-12345.upstash.io
Port: 6379
Password: AXX1XXxxxxxxxxxxxxxxxxxxxxxxxx
```

### **Step 4: Configure Render Environment Variables**
In your Render dashboard:

1. Go to your `shopping-assistant` service
2. **Environment** tab
3. Add these **Secret** environment variables:

```
REDIS_HOST = global-xyz-12345.upstash.io
REDIS_PORT = 6379
REDIS_PASSWORD = AXX1XXxxxxxxxxxxxxxxxxxxxxxxxx
```

**Important**: Set all Redis values as **"Secret"** (not visible in logs)

## 🔧 **Updated Configuration**

### **Your render.yaml (Already Updated):**
```yaml
services:
  - type: web
    name: shopping-assistant
    plan: free  # Free tier app
    envVars:
      - key: ENABLE_REDIS
        value: true  # ✅ Enable Redis
      - key: REDIS_HOST
        sync: false  # ✅ Set in dashboard as secret
      - key: REDIS_PORT
        sync: false  # ✅ Set in dashboard as secret
      - key: REDIS_PASSWORD
        sync: false  # ✅ Set in dashboard as secret
# No separate Redis service needed with Upstash!
```

### **Total Cost: $0/month** 🎉
- **Render App**: Free tier
- **Upstash Redis**: Free tier (10K commands/day)

## 🚀 **Deployment Process**

### **Step 1: Commit Configuration**
```bash
git add .
git commit -m "Configure Upstash Redis integration"
git push origin main
```

### **Step 2: Set Environment Variables**
1. Go to Render dashboard → Your service → Environment
2. Add the Upstash connection details as secrets:
   - `REDIS_HOST`: `your-upstash-endpoint.upstash.io`
   - `REDIS_PORT`: `6379`
   - `REDIS_PASSWORD`: `your-upstash-password`

### **Step 3: Deploy**
Render will automatically redeploy with the new configuration.

### **Step 4: Verify Connection**
Check your deployment logs for:
```
✅ Redis cache connected to global-xyz-12345.upstash.io:6379 (attempt 1)
```

## 📊 **Upstash Free Tier Limits**

| Feature | Free Tier | Paid Start |
|---------|-----------|------------|
| **Commands/Day** | 10,000 | 100K for $0.2 |
| **Bandwidth** | 200MB/day | Unlimited |
| **Connections** | 100 concurrent | 1000+ |
| **Storage** | 256MB | 1GB+ |
| **Regions** | All available | All available |

### **Is 10K Commands Enough?**
For your shopping assistant:
- **Preference extraction**: ~2-3 commands per query
- **Vector search cache**: ~5-10 commands per search
- **Session data**: ~1-2 commands per interaction

**Estimate**: 500-1000 user interactions per day on free tier ✅

## 🔍 **Advanced Configuration Options**

### **Option 1: Redis URL (Alternative)**
Instead of separate host/port/password, use a single URL:

```yaml
# In render.yaml
envVars:
  - key: REDIS_URL
    sync: false  # Set in dashboard
# Format: redis://:password@host:port
```

### **Option 2: REST API (Ultra-reliable)**
Upstash also provides REST API access:

```python
# Alternative connection method
import requests
import os

upstash_url = os.getenv('UPSTASH_REDIS_REST_URL')
upstash_token = os.getenv('UPSTASH_REDIS_REST_TOKEN')

# HTTP-based Redis commands
def redis_get(key):
    response = requests.get(
        f"{upstash_url}/get/{key}",
        headers={"Authorization": f"Bearer {upstash_token}"}
    )
    return response.json().get('result')
```

## 🛡️ **Security Best Practices**

### **Environment Variables Security:**
```yaml
# ✅ Correct - All Redis credentials as secrets
envVars:
  - key: REDIS_HOST
    sync: false  # Hidden from logs
  - key: REDIS_PASSWORD
    sync: false  # Hidden from logs

# ❌ Wrong - Never put credentials in plain text
envVars:
  - key: REDIS_PASSWORD
    value: AXX1XXxxxxxxxxxx  # Visible in logs!
```

### **Upstash Dashboard Security:**
- ✅ Enable **IP Allow List** (optional)
- ✅ Use **TLS/SSL** (enabled by default)
- ✅ Rotate passwords periodically
- ✅ Monitor usage in dashboard

## 📈 **Monitoring & Performance**

### **Upstash Console:**
1. Go to https://console.upstash.com/
2. Select your database
3. View real-time metrics:
   - Commands per second
   - Memory usage
   - Connection count
   - Error rates

### **Application Logs:**
Your app will show cache performance:
```
✅ Redis cache connected to global-xyz-12345.upstash.io:6379
🎯 Cache hit: preference extraction (saved 2.3s)
🎯 Cache hit: vector search (saved 1.8s)
🔄 Cache miss: new search query
```

### **Performance Optimization:**
```python
# Your main.py already optimized for Upstash:
# - Connection pooling
# - Retry logic
# - Timeout handling
# - Automatic fallback to memory cache
```

## 🆙 **Scaling Options**

### **When to Upgrade:**
- **Daily commands > 10K**: Upgrade to Pay-as-you-go
- **High traffic spikes**: Consider Pro plan
- **Multiple apps**: Dedicated cluster

### **Upstash Pricing:**
- **Pay-as-you-go**: $0.2 per 100K commands
- **Pro**: $120/month (unlimited)
- **Enterprise**: Custom pricing

## 🆘 **Troubleshooting**

### **Connection Issues:**
```bash
# Test Upstash connection locally
python -c "
import redis
r = redis.Redis(
    host='your-endpoint.upstash.io',
    port=6379,
    password='your-password',
    ssl=True
)
print('Connected:', r.ping())
"
```

### **Common Issues:**

#### **1. SSL/TLS Errors**
```python
# Upstash requires SSL, your main.py handles this
redis_config['ssl'] = True
redis_config['ssl_cert_reqs'] = None
```

#### **2. Timeout Errors**
```python
# Increase timeouts for serverless
redis_config['socket_connect_timeout'] = 10
redis_config['socket_timeout'] = 10
```

#### **3. Command Limit Exceeded**
- Monitor usage in Upstash console
- Optimize cache TTL values
- Consider upgrading to paid tier

### **Fallback Behavior:**
If Upstash is unreachable, your app automatically:
```
⚠️ Redis connection failed (connection timeout), using memory cache
```

## 🎯 **Migration from Render Redis**

### **Benefits of Switch:**
- ✅ **$7/month savings** (free tier)
- ✅ **Better performance** (global edge network)
- ✅ **More reliable** (99.99% uptime SLA)
- ✅ **Better monitoring** (detailed analytics)

### **What Changes:**
- ❌ Remove Render Redis service (save $7/month)
- ✅ Add Upstash connection details
- ✅ Same application code (no changes needed)

---

## 🎉 **Summary**

Your shopping assistant is now configured for **Upstash Redis**:

✅ **Free hosting** with Render  
✅ **Free Redis** with Upstash  
✅ **Total cost: $0/month**  
✅ **10K commands/day** (plenty for testing and demos)  
✅ **Global performance**  
✅ **Automatic fallback** to memory cache  

**Next Steps:**
1. Create Upstash account and database
2. Add connection details to Render environment variables
3. Deploy and enjoy persistent, high-performance caching! 🚀