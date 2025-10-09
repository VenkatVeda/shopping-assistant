#!/usr/bin/env python3
"""
Test Upstash Redis Connection
Run this script to verify your Upstash Redis configuration before starting the main app.
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_upstash_connection():
    """Test Upstash Redis connection with the same logic as main.py"""
    
    print("🧪 Testing Upstash Redis Connection...")
    print("=" * 50)
    
    # Check if Redis package is available
    try:
        import redis
        print("✅ Redis package available")
    except ImportError:
        print("❌ Redis package not found. Install with: pip install redis hiredis")
        return False
    
    # Check environment variables
    enable_redis = os.getenv('ENABLE_REDIS', 'true').lower() == 'true'
    redis_host = os.getenv('REDIS_HOST')
    redis_port = os.getenv('REDIS_PORT', '6379')
    redis_password = os.getenv('REDIS_PASSWORD')
    redis_url = os.getenv('REDIS_URL')
    
    print(f"ENABLE_REDIS: {enable_redis}")
    print(f"REDIS_HOST: {redis_host}")
    print(f"REDIS_PORT: {redis_port}")
    print(f"REDIS_PASSWORD: {'***' if redis_password else 'Not set'}")
    print(f"REDIS_URL: {'***' if redis_url else 'Not set'}")
    print()
    
    if not enable_redis:
        print("⚠️ Redis is disabled (ENABLE_REDIS=false)")
        return False
    
    if not redis_host and not redis_url:
        print("❌ No Redis connection details found")
        print("   Please set REDIS_HOST and REDIS_PASSWORD in your .env file")
        print("   Or set REDIS_URL for URL-based connection")
        return False
    
    try:
        # Connection configuration (same as main.py)
        redis_config = {
            'db': 0,
            'socket_connect_timeout': 10,
            'socket_timeout': 10,
            'retry_on_timeout': True,
            'health_check_interval': 30
        }
        
        # Handle different connection methods
        if redis_url:
            print(f"🔗 Connecting via Redis URL...")
            redis_client = redis.from_url(redis_url, **redis_config)
        else:
            print(f"🔗 Connecting to {redis_host}:{redis_port}...")
            redis_config.update({
                'host': redis_host,
                'port': int(redis_port)
            })
            
            if redis_password:
                redis_config['password'] = redis_password
            
            # Enable SSL for Upstash
            if '.upstash.io' in redis_host:
                redis_config['ssl'] = True
                redis_config['ssl_cert_reqs'] = None
                print("🔒 SSL enabled for Upstash connection")
            
            redis_client = redis.Redis(**redis_config)
        
        # Test connection
        print("🔄 Testing connection...")
        for attempt in range(3):
            try:
                result = redis_client.ping()
                if result:
                    print(f"✅ Connection successful! (attempt {attempt + 1})")
                    break
            except Exception as e:
                print(f"⚠️ Attempt {attempt + 1} failed: {e}")
                if attempt == 2:
                    raise e
        
        # Test basic operations
        print("\n🔄 Testing basic operations...")
        
        # Test SET
        test_key = "test:shopping_assistant"
        test_value = "Hello from Upstash!"
        redis_client.set(test_key, test_value, ex=60)  # Expires in 60 seconds
        print("✅ SET operation successful")
        
        # Test GET
        retrieved_value = redis_client.get(test_key)
        if retrieved_value:
            retrieved_value = retrieved_value.decode('utf-8')
            print(f"✅ GET operation successful: {retrieved_value}")
        
        # Test TTL
        ttl = redis_client.ttl(test_key)
        print(f"✅ TTL check successful: {ttl} seconds remaining")
        
        # Test complex data (like your app uses)
        import json
        import pickle
        
        complex_data = {
            "user_preferences": {"color": "blue", "brand": "nike"},
            "search_results": [{"id": 1, "name": "Blue Nike Bag"}],
            "timestamp": "2025-10-08"
        }
        
        # Test JSON serialization (for string storage)
        redis_client.set("test:json", json.dumps(complex_data), ex=60)
        json_result = json.loads(redis_client.get("test:json").decode('utf-8'))
        print("✅ JSON serialization test successful")
        
        # Test pickle serialization (like your cache uses)
        redis_client.set("test:pickle", pickle.dumps(complex_data), ex=60)
        pickle_result = pickle.loads(redis_client.get("test:pickle"))
        print("✅ Pickle serialization test successful")
        
        # Cleanup
        redis_client.delete(test_key, "test:json", "test:pickle")
        print("✅ Cleanup completed")
        
        print("\n🎉 All tests passed! Your Upstash Redis is ready.")
        print("\nNow you can run: python main.py")
        return True
        
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        print("\n🔧 Troubleshooting tips:")
        print("1. Check your REDIS_HOST, REDIS_PORT, and REDIS_PASSWORD in .env")
        print("2. Verify your Upstash database is active")
        print("3. Check your internet connection")
        print("4. Try regenerating the password in Upstash dashboard")
        return False

def print_usage_info():
    """Print information about Redis usage in the shopping assistant"""
    print("\n📊 Redis Usage in Shopping Assistant:")
    print("- User preferences caching")
    print("- Vector search results caching") 
    print("- Azure OpenAI response caching")
    print("- Session management")
    print("\n💰 Upstash Free Tier:")
    print("- 10,000 commands per day")
    print("- 256MB storage")
    print("- 100 concurrent connections")
    print("\n🚀 Performance Benefits:")
    print("- Faster repeat queries (cache hits)")
    print("- Reduced Azure OpenAI API calls")
    print("- Better user experience")

if __name__ == "__main__":
    print("🛍️ Shopping Assistant - Upstash Redis Test")
    print("=" * 50)
    
    success = test_upstash_connection()
    
    if success:
        print_usage_info()
        sys.exit(0)
    else:
        print("\n❌ Redis test failed. Please fix the issues above.")
        print("Your app will fall back to memory cache if Redis fails.")
        sys.exit(1)