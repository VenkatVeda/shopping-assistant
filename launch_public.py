#!/usr/bin/env python3
"""
Public Launch Script for Shopping Assistant
This script launches the Shopping Assistant with a public Gradio link
"""

from main import launch_production

if __name__ == "__main__":
    print("🌐 Launching Shopping Assistant with PUBLIC access...")
    print("⚠️  Note: This will create a public URL accessible from anywhere!")
    print("🔗 You'll receive a shareable link like: https://abcd1234.gradio.live")
    print("⏰ The public link will be active for 72 hours")
    print("\nStarting application...")
    
    # Launch with public sharing enabled
    launch_production()