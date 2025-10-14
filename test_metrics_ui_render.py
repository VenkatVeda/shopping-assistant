#!/usr/bin/env python3
"""Test metrics UI display on Render deployment"""

import os
import sys
import time
import json

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_metrics_rendering():
    """Test metrics rendering in UI"""
    print("🧪 Testing Metrics UI Display for Render Deployment")
    print("=" * 60)
    
    try:
        from main import ShoppingAssistantApp
        
        print("📦 Initializing Shopping Assistant...")
        app = ShoppingAssistantApp(enable_parallel=False)
        
        # Test metrics generation
        print("\n🔍 Testing metrics generation...")
        session_id, session_data = app.session_manager.get_or_create_session()
        
        # Test a simple query
        test_query = "Show me some leather bags under $100"
        print(f"🎯 Testing query: '{test_query}'")
        
        result, metrics = session_data.workflow.process_message(test_query, session_id)
        
        print(f"\n📊 Raw Metrics:")
        print(f"   • Type: {type(metrics)}")
        print(f"   • Content: {metrics}")
        
        if metrics:
            print(f"   • Tokens: {metrics.get('tokens', 'N/A')}")
            print(f"   • Latency: {metrics.get('latency', 'N/A')} seconds")
            print(f"   • Cost: ${metrics.get('cost', 'N/A')}")
        
        # Test how metrics are formatted in UI
        print(f"\n🎨 Testing UI formatting...")
        
        # Simulate what happens in gradio_interface.py
        response_text = result
        
        if app.session_manager.azure_service.is_langsmith_enabled():
            if metrics and 'tokens' in metrics:
                langsmith_info = f"\n\n<small style='color: #888; font-size: 0.75em; border-top: 1px solid #eee; padding-top: 5px;'>📊 Also tracked in LangSmith Dashboard | ⚡ Tokens: {metrics['tokens']} | ⏱️ {metrics['latency']:.2f}s | 💰 ${metrics['cost']:.4f}</small>"
            else:
                langsmith_info = f"\n\n<small style='color: #888; font-size: 0.75em; border-top: 1px solid #eee; padding-top: 5px;'>📊 Also tracked in LangSmith Dashboard</small>"
            response_text += langsmith_info
        
        print(f"\n✅ Final Response with Metrics:")
        print("-" * 40)
        print(response_text)
        print("-" * 40)
        
        # Check if HTML is being escaped
        if '<small' in response_text:
            print(f"✅ HTML metrics elements are present")
        else:
            print(f"❌ HTML metrics elements are missing")
        
        # Test gradio rendering
        print(f"\n🌐 Testing Gradio rendering...")
        
        # Test the actual chat interface method
        ui_result, ui_session_id = app.ui.chat_interface(test_query, session_id)
        
        print(f"📊 UI Result:")
        if ui_result:
            last_response = ui_result[-1][1] if ui_result else "No response"
            print(f"   • Last response includes metrics: {'<small' in last_response}")
            if '<small' in last_response:
                print(f"   ✅ Metrics HTML found in UI response")
                
                # Extract just the metrics part
                if '<small' in last_response and '</small>' in last_response:
                    start = last_response.find('<small')
                    end = last_response.find('</small>') + 8
                    metrics_html = last_response[start:end]
                    print(f"   📊 Metrics HTML: {metrics_html}")
            else:
                print(f"   ❌ No metrics HTML in UI response")
                print(f"   📝 Response content: {last_response[-200:]}")  # Last 200 chars
        
        print(f"\n🔍 Environment Check:")
        print(f"   • LangSmith enabled: {app.session_manager.azure_service.is_langsmith_enabled()}")
        print(f"   • LANGCHAIN_TRACING_V2: {os.getenv('LANGCHAIN_TRACING_V2', 'Not set')}")
        print(f"   • LANGCHAIN_API_KEY: {'Set' if os.getenv('LANGCHAIN_API_KEY') else 'Not set'}")
        print(f"   • LANGCHAIN_PROJECT: {os.getenv('LANGCHAIN_PROJECT', 'Not set')}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_render_specific_issues():
    """Test for Render-specific deployment issues"""
    print(f"\n🚀 Testing Render-Specific Issues")
    print("=" * 40)
    
    # Check for common Render issues
    issues = []
    
    # 1. Check if gradio is allowing HTML rendering
    try:
        import gradio as gr
        print(f"✅ Gradio version: {gr.__version__}")
        
        # Check if render_markdown is set correctly
        print(f"📝 Note: render_markdown should be False for HTML to work")
        
    except Exception as e:
        issues.append(f"Gradio import issue: {e}")
    
    # 2. Check environment variables
    required_vars = [
        'AZURE_OPENAI_API_KEY',
        'AZURE_OPENAI_ENDPOINT',
        'LANGCHAIN_TRACING_V2',
        'LANGCHAIN_API_KEY',
        'LANGCHAIN_PROJECT'
    ]
    
    print(f"\n🔧 Environment Variables Check:")
    for var in required_vars:
        value = os.getenv(var)
        if value:
            print(f"   ✅ {var}: Set ({'...' + value[-10:] if len(value) > 10 else value})")
        else:
            print(f"   ❌ {var}: Not set")
            issues.append(f"Missing {var}")
    
    # 3. Check for CSS rendering
    print(f"\n🎨 CSS Check for Metrics:")
    css_test = """
    <small style='color: #888; font-size: 0.75em; border-top: 1px solid #eee; padding-top: 5px;'>
        📊 Test Metrics: Tokens: 123 | ⏱️ 1.23s | 💰 $0.0045
    </small>
    """
    print(f"   📊 Test HTML: {css_test.strip()}")
    
    if issues:
        print(f"\n⚠️ Found {len(issues)} potential issues:")
        for i, issue in enumerate(issues, 1):
            print(f"   {i}. {issue}")
    else:
        print(f"\n✅ No obvious deployment issues detected")
    
    return len(issues) == 0

if __name__ == "__main__":
    print("🔬 Metrics UI Testing for Render Deployment")
    print("=" * 50)
    
    success1 = test_metrics_rendering()
    success2 = test_render_specific_issues()
    
    if success1 and success2:
        print(f"\n🎉 All tests passed! Metrics should be visible on Render.")
    else:
        print(f"\n⚠️ Some tests failed. Check the output above for issues.")
    
    print(f"\n💡 If metrics still don't show on Render:")
    print(f"   1. Check that render_markdown=False in gradio chatbot")
    print(f"   2. Verify all environment variables are set")
    print(f"   3. Check Render logs for HTML escaping warnings")
    print(f"   4. Consider adding a dedicated metrics display component")