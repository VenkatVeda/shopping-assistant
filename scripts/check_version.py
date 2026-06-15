"""
Version Check - Determine which code is running (old vs new)
"""

import os
import sys

print("="*70)
print("SHOPPING ASSISTANT - VERSION CHECK")
print("="*70)

# Check 1: New extended tools files exist?
print("\n1. Checking for NEW integration files...")
new_files = [
    "core/extended_tools/__init__.py",
    "core/extended_tools/direct_tools.py",
]

all_exist = True
for file in new_files:
    exists = os.path.exists(file)
    status = "✅ FOUND" if exists else "❌ MISSING"
    print(f"   {status}: {file}")
    if not exists:
        all_exist = False

if all_exist:
    print("\n   ✅ NEW integration files are present")
else:
    print("\n   ❌ NEW integration files are missing - using OLD code")

# Check 2: Environment variable set?
print("\n2. Checking ENABLE_EXTENDED_MCP_TOOLS...")
enabled = os.getenv("ENABLE_EXTENDED_MCP_TOOLS", "not set")
print(f"   Value: {enabled}")

if enabled.lower() == "true":
    print("   ✅ NEW extended tools are ENABLED")
elif enabled == "not set":
    print("   ⚠️  Variable not set - NEW extended tools are DISABLED")
else:
    print("   ⚠️  Variable is false - NEW extended tools are DISABLED")

# Check 3: Can we import new extended tools?
print("\n3. Testing NEW extended tools import...")
try:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'core'))
    from extended_tools import get_extended_tools, is_databricks_app
    print("   ✅ NEW extended tools module imports successfully")
    
    # Check environment
    is_databricks = is_databricks_app()
    print(f"   Environment detected: {'Databricks Apps' if is_databricks else 'Local'}")
    
    # Try to get tools
    if enabled.lower() == "true":
        tools = get_extended_tools()
        if tools:
            print(f"   ✅ NEW extended tools initialized: {type(tools).__name__}")
            
            # Test it
            if tools.health_check():
                print("   ✅ NEW extended tools are HEALTHY and WORKING")
        else:
            print("   ⚠️  NEW extended tools returned None (might be disabled)")
    else:
        print("   ⏸️  Skipping initialization (feature disabled)")
    
except ImportError as e:
    print(f"   ❌ Cannot import NEW extended tools: {e}")
    print("   📌 This means OLD code is running")

# Check 4: Old vector search still works?
print("\n4. Checking OLD vector search (should be unchanged)...")
try:
    # Don't fully import to avoid loading everything
    old_vector_file = "core/mcp_client/vector_search_client.py"
    if os.path.exists(old_vector_file):
        print(f"   ✅ OLD vector search file exists: {old_vector_file}")
        print("   ✅ OLD vector search should still work (unchanged)")
    else:
        print(f"   ❌ OLD vector search file missing")
except Exception as e:
    print(f"   ⚠️  Error checking: {e}")

# Check 5: MCP server code accessible?
print("\n5. Checking MCP server code (for direct imports)...")
mcp_server_path = "mcp-server-custom-code/src/custom_server/tools"
if os.path.exists(mcp_server_path):
    print(f"   ✅ MCP server code found: {mcp_server_path}")
    
    # List tools
    tools_dir = os.listdir(mcp_server_path)
    py_files = [f for f in tools_dir if f.endswith('.py') and not f.startswith('__')]
    print(f"   ✅ Available tool modules: {', '.join(py_files)}")
else:
    print(f"   ❌ MCP server code not found: {mcp_server_path}")
    print("   ⚠️  Direct imports won't work without this")

# Check 6: What's in workflow?
print("\n6. Checking if workflow uses NEW extended tools...")
workflow_file = "core/workflow.py"
if os.path.exists(workflow_file):
    with open(workflow_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if "extended_tools" in content:
        print("   ✅ Workflow mentions 'extended_tools' - likely using NEW code")
    else:
        print("   ⚠️  Workflow doesn't mention 'extended_tools'")
        print("   📌 Workflow is using OLD code (extended tools not integrated)")
else:
    print(f"   ❌ Workflow file not found")

# FINAL VERDICT
print("\n" + "="*70)
print("VERDICT")
print("="*70)

if all_exist and enabled.lower() == "true":
    print("\n✅ NEW CODE IS ACTIVE")
    print("\nYou have:")
    print("  • NEW extended tools files present")
    print("  • Environment variable enabled")
    print("  • Can import and use new features")
    print("\nNew features available:")
    print("  • Stock market data (tools.get_stock_info)")
    print("  • SQL queries (tools.run_databricks_query)")
    print("  • Admin tools (vector config)")
    
elif all_exist and enabled.lower() != "true":
    print("\n⚠️  NEW CODE INSTALLED BUT DISABLED")
    print("\nYou have:")
    print("  • NEW extended tools files present")
    print("  • But ENABLE_EXTENDED_MCP_TOOLS not set to 'true'")
    print("\nTo enable NEW features:")
    print('  Set: ENABLE_EXTENDED_MCP_TOOLS=true')
    
elif not all_exist:
    print("\n❌ OLD CODE IS RUNNING")
    print("\nYou are using:")
    print("  • Original implementation")
    print("  • No extended tools integration")
    print("\nTo use NEW code:")
    print("  1. Ensure integration files are synced to Databricks")
    print("  2. Set ENABLE_EXTENDED_MCP_TOOLS=true")
    
else:
    print("\n❓ UNCLEAR STATUS")
    print("\nCheck the details above to diagnose")

print("\n" + "="*70)

# Quick test command
print("\nQUICK TEST:")
if all_exist:
    print("\nIn Python console, try:")
    print("  >>> import os")
    print("  >>> os.environ['ENABLE_EXTENDED_MCP_TOOLS'] = 'true'")
    print("  >>> from core.extended_tools import get_extended_tools")
    print("  >>> tools = get_extended_tools()")
    print("  >>> if tools: print('NEW CODE WORKING!'); print(tools.get_stock_info('TCS'))")
else:
    print("\nNew integration not detected. Review sync and deployment.")

print("="*70)
