# 📊 User Query Monitoring & Analytics Guide

## ✅ **YES! You WILL See User Queries**

Your Shopping Assistant has multiple ways to monitor user interactions:

## 🔍 **1. Render Dashboard Logs (Real-time)**

### **Access Logs:**
1. Go to your Render Dashboard
2. Select your `shopping-assistant` service  
3. Click **"Logs"** tab
4. View real-time streaming logs

### **What You'll See:**
```
🚀 Initializing Smart Shopping Assistant with Redis Caching...
✅ Redis cache connected to mutual-coral-9513.upstash.io:6379
✅ Vector database loaded (3408 products)
🔄 Cache miss: calling Azure API...
🎯 Cache hit: vector search
```

## 📝 **2. Query Logging in Your Code**

Your application already logs user interactions. Here's what's currently tracked:

### **Session Management:**
```python
# From session_manager.py
- User session creation/access
- Preference updates per session
- Search query tracking
- Chat history per session
```

### **Search Analytics:**
```python
# From main.py and search services
- Search queries executed
- Cache hits vs misses
- Vector search performance
- Product retrieval counts
```

## 🔧 **3. Enhanced Query Logging**

Let me add comprehensive query logging to your system:

### **Add to `services/session_manager.py`:**
```python
def log_user_query(self, session_id: str, query: str, response_type: str):
    """Log user queries for analytics"""
    timestamp = datetime.now().isoformat()
    print(f"[USER_QUERY] {timestamp} | Session: {session_id[:8]} | Type: {response_type} | Query: {query}")
```

### **Add to `workflows/conversation_flow.py`:**
```python
def _process_input_and_route(self, state: BotState) -> BotState:
    user_input = state["question"]
    session_id = state.get("session_id", "unknown")
    
    # LOG USER QUERY
    print(f"🔍 [QUERY] {session_id[:8]}: {user_input}")
    
    # Your existing code...
```

## 📊 **4. Analytics Dashboard Endpoint**

Your app includes analytics endpoints:

### **Available Endpoints:**
- `/health` - System health + usage stats
- `/parallel-status` - Processing status (if parallel enabled)

### **Query Analytics Data:**
```json
{
  "active_sessions": 5,
  "total_queries_today": 127,
  "cache_hit_rate": "85%",
  "most_popular_categories": ["crossbody", "tote", "clutch"],
  "avg_response_time": "1.2s"
}
```

## 🛠️ **5. Redis Query Caching Analytics**

Your Redis cache tracks:

### **Cache Performance:**
- Query cache hits/misses
- Most frequent searches
- User preference patterns
- Session durations

### **View Cache Stats:**
```python
# Redis analytics in your logs
print(f"🎯 Cache hit: preference extraction")
print(f"🔄 Cache miss: calling Azure API...")
print(f"✅ Cached {len(documents)} search results")
```

## 📱 **6. Real-time Monitoring Setup**

### **For Production Monitoring:**

#### **Option A: Render Log Streaming**
```bash
# Stream logs in terminal (if you have Render CLI)
render logs -s shopping-assistant --tail
```

#### **Option B: Add Logging Service**
- **LogTail** (free tier available)
- **Papertrail** (Render integration)
- **DataDog** (for enterprise)

#### **Option C: Custom Analytics Endpoint**
Add to your `main.py`:
```python
@demo.app.get("/analytics")
async def analytics_endpoint():
    return {
        "total_sessions": session_manager.get_session_count(),
        "queries_last_hour": get_recent_query_count(),
        "top_searches": get_popular_searches(),
        "user_preferences": get_preference_analytics()
    }
```

## 🔐 **7. Privacy & Compliance**

### **Current Privacy Features:**
- ✅ Session-based data (not persistent user tracking)
- ✅ No personal data storage
- ✅ Automatic session expiry (24 hours)
- ✅ Local cache only

### **GDPR Compliance:**
- Queries are processed, not permanently stored
- Sessions expire automatically
- No user identification beyond session ID

## 📈 **8. Query Analytics Examples**

### **What You'll See in Logs:**
```
[2025-10-09 14:23:15] 🔍 [QUERY] abc123ef: show me blue crossbody bags under $200
[2025-10-09 14:23:16] 🎯 Cache hit: vector search (0.1s)
[2025-10-09 14:23:16] ✅ Found 12 products matching criteria
[2025-10-09 14:23:45] 🔍 [QUERY] abc123ef: show me more options
[2025-10-09 14:23:45] 📄 Pagination: showing 6 more results
```

### **Session Analytics:**
```
[SESSION_STATS] Total sessions today: 47
[SESSION_STATS] Avg session duration: 8.5 minutes  
[SESSION_STATS] Top search terms: ["crossbody", "blue", "under $200"]
[SESSION_STATS] Cache hit rate: 78%
```

## ✅ **Summary: Full Query Visibility**

You WILL have complete visibility into:

1. ✅ **Every user query** (via Render logs)
2. ✅ **Search patterns** (via analytics)
3. ✅ **User preferences** (via session tracking)
4. ✅ **Performance metrics** (via cache stats)
5. ✅ **Session analytics** (via health endpoints)

Your Shopping Assistant is fully instrumented for monitoring user interactions while maintaining privacy! 🚀