"""
Test the deployed Databricks app to verify extended tools are working
"""
import requests
import json
import sys

# Your Databricks App URL
APP_URL = input("Enter your Databricks App URL (e.g., https://adb-xxx.azuredatabricks.net/...): ").strip()

if not APP_URL:
    print("❌ App URL is required")
    sys.exit(1)

# Remove trailing slash
APP_URL = APP_URL.rstrip('/')

print(f"\n🔍 Testing: {APP_URL}")
print("=" * 80)

# Test 1: Basic health check
print("\n1️⃣ Testing basic health endpoint...")
try:
    response = requests.get(f"{APP_URL}/api/health", timeout=10)
    if response.status_code == 200:
        print(f"✅ Health check passed")
        print(f"   Response: {response.json()}")
    else:
        print(f"❌ Health check failed: {response.status_code}")
except Exception as e:
    print(f"❌ Health check error: {e}")

# Test 2: Extended tools endpoint
print("\n2️⃣ Testing extended tools endpoint...")
try:
    response = requests.get(f"{APP_URL}/api/test-extended-tools", timeout=30)
    
    if response.status_code == 200:
        data = response.json()
        print(f"✅ Extended tools endpoint responded")
        print("\n📊 Results:")
        print(json.dumps(data, indent=2))
        
        # Analyze results
        print("\n" + "=" * 80)
        print("📋 Analysis:")
        
        status = data.get("status")
        if status == "enabled":
            print("✅ Extended tools are ENABLED")
            
            tool_mode = data.get("tool_mode")
            print(f"✅ Tool mode: {tool_mode}")
            
            if tool_mode == "DirectExtendedTools":
                print("   ✓ Using DirectExtendedTools (correct for Databricks Apps)")
            elif tool_mode == "ExtendedToolsClient":
                print("   ⚠️  Using HTTP mode (unexpected on Databricks Apps)")
            
            health = data.get("health_check")
            if health:
                print(f"✅ Health check: {health}")
            
            stock_test = data.get("stock_tool_test", {})
            if stock_test.get("status") == "working":
                print("✅ Stock tool is working!")
                print(f"   Sample: {stock_test.get('sample', '')[:100]}...")
            else:
                print(f"❌ Stock tool error: {stock_test.get('error')}")
            
            env = data.get("environment", {})
            print(f"\n🌍 Environment:")
            print(f"   Is Databricks: {env.get('is_databricks')}")
            print(f"   Feature flag: {env.get('ENABLE_EXTENDED_MCP_TOOLS')}")
            
        elif status == "disabled":
            print("❌ Extended tools are DISABLED")
            print(f"   Message: {data.get('message')}")
            env = data.get("environment", {})
            print(f"   Env var value: {env.get('ENABLE_EXTENDED_MCP_TOOLS')}")
            
        else:
            print(f"❌ Unexpected status: {status}")
            
    else:
        print(f"❌ Endpoint returned error: {response.status_code}")
        try:
            print(f"   Error: {response.json()}")
        except:
            print(f"   Response: {response.text[:200]}")
            
except Exception as e:
    print(f"❌ Request failed: {e}")

print("\n" + "=" * 80)
print("\n✅ Testing complete!")
print("\nWhat to look for:")
print("  ✓ Status should be 'enabled'")
print("  ✓ Tool mode should be 'DirectExtendedTools' on Databricks")
print("  ✓ Health check should return success message")
print("  ✓ Stock tool test should show market data")
