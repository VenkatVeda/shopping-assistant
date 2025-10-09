# 🧪 Local Testing with Upstash Redis

## 🎯 **Local Testing Setup**

### **Step 1: Get Upstash Credentials**
1. Go to https://upstash.com/ and create account
2. Create new Redis database:
   - **Name**: `shopping-assistant-test`
   - **Region**: Choose closest to you
   - **Type**: Regional (free)
3. Copy the connection details from dashboard

### **Step 2: Update .env File**
Replace the placeholder values in your `.env` file:

```env
# Redis Configuration (Upstash)
ENABLE_REDIS=true
REDIS_HOST=global-xyz-12345.upstash.io  # ← Replace with your endpoint
REDIS_PORT=6379
REDIS_PASSWORD=AXX1XXxxxxxxxxxxxxxxxxxxxxxxxx  # ← Replace with your password
```

### **Step 3: Test Connection**
Run this simple test to verify Upstash connection:

```bash
python -c "
import redis
import os
from dotenv import load_dotenv

load_dotenv()

try:
    r = redis.Redis(
        host=os.getenv('REDIS_HOST'),
        port=int(os.getenv('REDIS_PORT')),
        password=os.getenv('REDIS_PASSWORD'),
        ssl=True,
        ssl_cert_reqs=None
    )
    print('Testing connection...')
    result = r.ping()
    print('✅ Upstash Redis connected successfully!')
    
    # Test basic operations
    r.set('test_key', 'Hello from local!')
    value = r.get('test_key').decode('utf-8')
    print(f'✅ Test value: {value}')
    r.delete('test_key')
    print('✅ Test cleanup complete')
    
except Exception as e:
    print(f'❌ Connection failed: {e}')
    print('Check your REDIS_HOST, REDIS_PORT, and REDIS_PASSWORD in .env')
"
```

### **Step 4: Run Your Application**
```bash
# Start your shopping assistant locally
python main.py

# Look for this in the logs:
# ✅ SSL enabled for Upstash connection
# ✅ Redis cache connected to global-xyz-12345.upstash.io:6379 (attempt 1)
```

## 🔧 **Alternative Testing Options**

### **Option A: Test with Local Redis First**
If you want to test with local Redis before Upstash:

```env
# For local Redis testing
ENABLE_REDIS=true
REDIS_HOST=localhost
REDIS_PORT=6379
# REDIS_PASSWORD=  # Leave empty for local Redis
```

Start local Redis:
```bash
# Option 1: Docker
docker run -d --name redis-test -p 6379:6379 redis:7-alpine

# Option 2: Use your existing docker-compose
docker-compose up -d redis
```

### **Option B: Disable Redis for Initial Testing**
```env
# Test without Redis first
ENABLE_REDIS=false
```

Your app will use memory cache and you can verify everything else works.

## 📊 **Local Testing Checklist**

### **✅ Basic Functionality:**
- [ ] Application starts without errors
- [ ] Cache system initializes (Redis or memory)
- [ ] Vector database loads successfully
- [ ] Azure OpenAI connection works
- [ ] Gradio interface launches

### **✅ Cache Testing:**
- [ ] First query (cache miss) works
- [ ] Repeat query (cache hit) is faster
- [ ] Cache performance logs show hits/misses
- [ ] Session data persists across requests

### **✅ Redis-Specific Testing:**
```python
# Test these scenarios:
# 1. Fresh start (empty cache)
# 2. Cached preference extraction
# 3. Cached vector search results
# 4. Cache expiration (TTL)
# 5. Cache fallback if Redis fails
```

## 🐛 **Troubleshooting Local Setup**

### **Common Issues:**

#### **1. SSL Certificate Errors**
```
SSLError: [SSL: CERTIFICATE_VERIFY_FAILED]
```
**Solution**: Your main.py already handles this:
```python
redis_config['ssl_cert_reqs'] = None  # Disables cert verification
```

#### **2. Connection Timeout**
```
TimeoutError: Operation timed out
```
**Solutions**:
- Check internet connection
- Verify Upstash endpoint is correct
- Try different region if needed

#### **3. Authentication Failed**
```
AuthenticationError: Invalid password
```
**Solutions**:
- Double-check REDIS_PASSWORD in .env
- Ensure no extra spaces in password
- Try regenerating password in Upstash dashboard

#### **4. Import Errors**
```
ModuleNotFoundError: No module named 'redis'
```
**Solution**:
```bash
pip install redis hiredis
```

### **Environment Debugging:**
```python
# Add this to your main.py temporarily for debugging
print("=== Redis Configuration ===")
print(f"ENABLE_REDIS: {os.getenv('ENABLE_REDIS')}")
print(f"REDIS_HOST: {os.getenv('REDIS_HOST')}")
print(f"REDIS_PORT: {os.getenv('REDIS_PORT')}")
print(f"REDIS_PASSWORD: {'***' if os.getenv('REDIS_PASSWORD') else 'Not set'}")
print("========================")
```

## 📈 **Performance Testing**

### **Cache Performance Comparison:**
```bash
# Test 1: First run (cold cache)
time python -c "from main import *; test_query()"

# Test 2: Second run (warm cache)  
time python -c "from main import *; test_query()"

# You should see significant speedup on second run
```

### **Expected Performance:**
- **First query**: 2-5 seconds (API calls + caching)
- **Cached query**: 200-500ms (Redis retrieval)
- **Memory fallback**: 50-100ms (local memory)

## 🚀 **Deploy After Local Success**

Once local testing works perfectly:

1. **Commit your changes:**
```bash
git add .env
git commit -m "Add Upstash Redis configuration for local testing"
```

2. **Set production environment variables in Render:**
- Copy same values from your .env to Render dashboard
- Set as "Secret" variables in production

3. **Deploy:**
```bash
git push origin main
```

4. **Monitor deployment logs** for same success messages

## 🎯 **Local vs Production**

| Environment | Redis Config | Cost | Performance |
|-------------|--------------|------|-------------|
| **Local** | Same Upstash | Free | Great |
| **Production** | Same Upstash | Free | Great |
| **Development** | Local Redis | Free | Excellent |

**Advantage**: Using same Upstash instance for local and production ensures identical behavior!

---

**Test locally first, deploy with confidence! 🎉**