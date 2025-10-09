#!/usr/bin/env python3

"""
Debug Show More Button - Simple test to verify button functionality
"""

import gradio as gr

def create_simple_test():
    """Create a simple test interface to verify button functionality"""
    
    with gr.Blocks() as demo:
        gr.HTML("<h1>Show More Button Test</h1>")
        
        # Test button
        test_btn = gr.Button("Show More Results", visible=True, variant="primary")
        status_text = gr.Markdown("Click the button above to test")
        
        def handle_click():
            return "Button clicked successfully! ✅"
        
        test_btn.click(
            fn=handle_click,
            outputs=[status_text]
        )
    
    return demo

if __name__ == "__main__":
    demo = create_simple_test()
    demo.launch(
        server_name="127.0.0.1",
        server_port=7861,  # Different port
        share=False,
        debug=True
    )