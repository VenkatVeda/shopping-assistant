#!/usr/bin/env python3
"""
Advanced Upstash Redis Connection Test
Tests multiple connection methods to find the working configuration.
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_rest_api():
    """Test Upstash REST API (HTTP-based Redis)"""
    print("🌐 Testing Upstash REST API...")
    
    try:
        # Extract credentials from Redis URL
        redis_url = os.getenv('REDIS_URL', '')
        if not redis_url:
            print("❌ No REDIS_URL found")
            return False
        
        # Parse Redis URL: redis://default:password@host:port
        # Format: redis://username:password@host:port
        if redis_url.startswith('redis://'):
            url_parts = redis_url[8:]  # Remove 'redis://'
            if '@' in url_parts:
                auth_part, host_part = url_parts.split('@')
                if ':' in auth_part:
                    username, password = auth_part.split(':', 1)
                else:
                    username = 'default'
                    password = auth_part
                
                host = host_part.split(':')[0]
                
                print(f"Extracted - Host: {host}, Username: {username}")
                
                # Test REST API
                rest_url = f"https://{host}/ping"
                headers = {
                    'Authorization': f'Bearer {password}',
                    'Content-Type': 'application/json'
                }
                
                request = urllib.request.Request(rest_url, headers=headers, method='POST')
                
                with urllib.request.urlopen(request) as response:
                    result = json.loads(response.read().decode())
                    print(f"✅ REST API Response: {result}")
                    return True
        
        return False
        
    except Exception as e:
        print(f"❌ REST API failed: {e}")
        return False

def test_redis_tcp_connection():
    """Test Redis TCP connection with different configurations"""
    print("\n🔗 Testing Redis TCP Connection...")
    
    try:
        import redis
        
        redis_url = os.getenv('REDIS_URL', '')
        if not redis_url:
            print("❌ No REDIS_URL found")
            return False
        
        # Test different SSL configurations
        ssl_configs = [
            # Standard SSL
            {'ssl': True, 'ssl_cert_reqs': None},
            # Strict SSL
            {'ssl': True},
            # No SSL (might work for some)
            {},
        ]
        
        for i, ssl_config in enumerate(ssl_configs, 1):
            try:
                print(f"\n🔄 Attempt {i}: {ssl_config}")
                
                client = redis.from_url(
                    redis_url,
                    socket_connect_timeout=10,
                    socket_timeout=10,
                    retry_on_timeout=True,
                    **ssl_config
                )
                
                result = client.ping()
                if result:
                    print(f"✅ TCP Connection successful with config: {ssl_config}")
                    
                    # Test basic operations
                    client.set('test_key', 'test_value', ex=60)
                    value = client.get('test_key')
                    client.delete('test_key')
                    
                    print(f"✅ Basic operations successful: {value}")
                    return True
                    
            except Exception as e:
                print(f"⚠️ Attempt {i} failed: {e}")
                continue
        
        print("❌ All TCP connection attempts failed")
        return False
        
    except ImportError:
        print("❌ Redis package not available")
        return False
    except Exception as e:
        print(f"❌ TCP connection test failed: {e}")
        return False

def test_alternative_url_formats():
    """Test different Redis URL formats"""
    print("\n🔧 Testing Alternative URL Formats...")
    
    try:
        import redis
        
        redis_url = os.getenv('REDIS_URL', '')
        if not redis_url:
            return False
        
        # Extract components
        if redis_url.startswith('redis://'):
            url_parts = redis_url[8:]  # Remove 'redis://'
            if '@' in url_parts:
                auth_part, host_part = url_parts.split('@')
                if ':' in auth_part:
                    username, password = auth_part.split(':', 1)
                else:
                    username = 'default'
                    password = auth_part
                
                host_port = host_part.split(':')
                host = host_port[0]
                port = int(host_port[1]) if len(host_port) > 1 else 6379
                
                # Try different URL formats
                url_formats = [
                    f"redis://{username}:{password}@{host}:{port}",
                    f"redis://:{password}@{host}:{port}",
                    f"rediss://{username}:{password}@{host}:{port}",  # SSL
                    f"rediss://:{password}@{host}:{port}",  # SSL
                ]
                
                for i, url in enumerate(url_formats, 1):
                    try:
                        print(f"\n🔄 Format {i}: {url[:20]}...{url[-20:]}")
                        
                        client = redis.from_url(
                            url,
                            socket_connect_timeout=10,
                            socket_timeout=10,
                            ssl_cert_reqs=None if 'rediss://' in url else None
                        )
                        
                        result = client.ping()
                        if result:
                            print(f"✅ URL format {i} successful!")
                            return True
                            
                    except Exception as e:
                        print(f"⚠️ Format {i} failed: {str(e)[:100]}...")
                        continue
        
        return False
        
    except Exception as e:
        print(f"❌ URL format test failed: {e}")
        return False

def main():
    print("🛍️ Advanced Upstash Redis Connection Test")
    print("=" * 60)
    
    # Test 1: REST API (most reliable)
    rest_success = test_rest_api()
    
    # Test 2: TCP Connection
    tcp_success = test_redis_tcp_connection()
    
    # Test 3: Alternative URL formats
    alt_success = test_alternative_url_formats()
    
    print("\n" + "=" * 60)
    print("📊 Test Results Summary:")
    print(f"REST API: {'✅ Success' if rest_success else '❌ Failed'}")
    print(f"TCP Connection: {'✅ Success' if tcp_success else '❌ Failed'}")
    print(f"Alternative Formats: {'✅ Success' if alt_success else '❌ Failed'}")
    
    if rest_success:
        print("\n🎉 Good news! Upstash REST API works.")
        print("💡 Recommendation: Use REST API for now, TCP for production later.")
        
        print("\n🔧 Update your main.py to use REST API as fallback:")
        print("Consider using upstash-redis package: pip install upstash-redis")
        
    elif tcp_success:
        print("\n🎉 TCP connection works! Your app should work normally.")
        
    else:
        print("\n⚠️ Neither method worked. Possible issues:")
        print("1. Upstash database might be paused/inactive")
        print("2. Network/firewall blocking connections")
        print("3. Credentials might be incorrect")
        print("4. Region restrictions")
        
        print("\n🛠️ Next steps:")
        print("1. Check Upstash dashboard - is database active?")
        print("2. Try regenerating credentials")
        print("3. Your app will fall back to memory cache (still works!)")

if __name__ == "__main__":
    main()