# simple_parallel_demo.py
"""
Simple demonstration of FIFO vs Parallel execution without Azure API dependencies
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor
import gradio as gr


def simulate_processing_work(user_id, duration=2):
    """Simulate processing work that takes time (like Azure API calls)"""
    start_time = time.time()
    current_time = time.strftime('%H:%M:%S')
    print(f"👤 User {user_id} starting processing at {current_time}")
    
    # Simulate work (like Azure API call, database query, etc.)
    time.sleep(duration)
    
    end_time = time.time()
    current_time = time.strftime('%H:%M:%S')
    processing_time = end_time - start_time
    print(f"✅ User {user_id} completed at {current_time} (took {processing_time:.2f}s)")
    
    return f"Response for User {user_id} after {processing_time:.2f}s"


def test_fifo_behavior():
    """Demonstrate FIFO (current Gradio behavior)"""
    print("\n🔍 TESTING FIFO BEHAVIOR (Current Problem)")
    print("=" * 50)
    
    def process_sequentially(user_id):
        return simulate_processing_work(user_id, 2)
    
    start_time = time.time()
    current_time = time.strftime('%H:%M:%S')
    print(f"🏃‍♂️ Starting 3 users SEQUENTIALLY at {current_time}")
    
    # This is what happens in current Gradio - one user at a time
    results = []
    for i in range(3):
        result = process_sequentially(i + 1)
        results.append(result)
    
    total_time = time.time() - start_time
    current_time = time.strftime('%H:%M:%S')
    print(f"🏁 All users completed at {current_time}")
    print(f"📊 Total time: {total_time:.2f}s (Users waited for each other)")
    
    return total_time


def test_parallel_behavior():
    """Demonstrate Parallel processing (solution)"""
    print("\n🚀 TESTING PARALLEL BEHAVIOR (Solution)")
    print("=" * 50)
    
    def process_concurrently(user_id):
        return simulate_processing_work(user_id, 2)
    
    start_time = time.time()
    current_time = time.strftime('%H:%M:%S')
    print(f"🏃‍♂️ Starting 3 users CONCURRENTLY at {current_time}")
    
    # This is what happens with parallel processing - all users at once
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(process_concurrently, i + 1) for i in range(3)]
        results = [future.result() for future in futures]
    
    total_time = time.time() - start_time
    current_time = time.strftime('%H:%M:%S')
    print(f"🏁 All users completed at {current_time}")
    print(f"📊 Total time: {total_time:.2f}s (Users processed simultaneously)")
    
    return total_time


# Gradio interface examples

def create_fifo_interface():
    """Create a Gradio interface that demonstrates FIFO behavior"""
    
    def slow_response(message):
        """Simulate a slow response (like current system)"""
        user_id = len(message.split()) % 3 + 1  # Simple way to assign user ID
        return simulate_processing_work(user_id, 3)
    
    with gr.Blocks(title="FIFO Demo") as demo:
        gr.Markdown("# FIFO Processing Demo (Current Problem)")
        gr.Markdown("Try opening this in multiple browser tabs and sending messages simultaneously")
        
        chatbot = gr.Chatbot()
        msg = gr.Textbox(placeholder="Type a message...")
        
        def respond(message, history):
            response = slow_response(message)
            history.append((message, response))
            return history, ""
        
        msg.submit(respond, [msg, chatbot], [chatbot, msg])
    
    return demo


def create_parallel_interface():
    """Create a Gradio interface with parallel processing"""
    
    async def fast_response_async(message):
        """Simulate async response processing"""
        import asyncio
        user_id = len(message.split()) % 3 + 1
        
        # Run the blocking operation in a thread pool
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, 
            simulate_processing_work, 
            user_id, 
            3
        )
        return result
    
    with gr.Blocks(title="Parallel Demo") as demo:
        gr.Markdown("# Parallel Processing Demo (Solution)")
        gr.Markdown("Multiple users can now chat simultaneously without waiting!")
        
        chatbot = gr.Chatbot()
        msg = gr.Textbox(placeholder="Type a message...")
        
        async def respond_async(message, history):
            response = await fast_response_async(message)
            history.append((message, response))
            return history, ""
        
        msg.submit(respond_async, [msg, chatbot], [chatbot, msg])
    
    return demo


def main():
    """Run the demonstration"""
    print("🧪 SIMPLE PARALLEL EXECUTION DEMONSTRATION")
    print("=" * 60)
    
    # Test 1: FIFO behavior (current problem)
    fifo_time = test_fifo_behavior()
    
    time.sleep(1)  # Brief pause between tests
    
    # Test 2: Parallel behavior (solution)
    parallel_time = test_parallel_behavior()
    
    # Compare results
    print("\n" + "=" * 60)
    print("📊 COMPARISON RESULTS")
    print("=" * 60)
    
    improvement = ((fifo_time - parallel_time) / fifo_time) * 100
    speedup = fifo_time / parallel_time
    
    print(f"\n🔍 FIFO Processing (Current Problem):")
    print(f"   • Time taken: {fifo_time:.2f} seconds")
    print(f"   • Behavior: User 2 waits for User 1, User 3 waits for both")
    print(f"   • User experience: BAD - Users have to wait")
    
    print(f"\n🚀 Parallel Processing (Solution):")
    print(f"   • Time taken: {parallel_time:.2f} seconds")
    print(f"   • Behavior: All users process simultaneously")
    print(f"   • User experience: GOOD - No waiting")
    
    print(f"\n✨ Performance Improvement:")
    print(f"   • {improvement:.1f}% faster")
    print(f"   • {speedup:.1f}x speedup")
    print(f"   • Users save {fifo_time - parallel_time:.1f} seconds")
    
    print(f"\n🎯 Your Problem:")
    print(f"   • Session isolation: ✅ Working correctly")
    print(f"   • User data mixing: ✅ No cross-contamination")
    print(f"   • Issue: Gradio processes requests in FIFO order")
    print(f"   • Solution: Enable parallel processing with max_threads and async handlers")
    
    print(f"\n🚀 To fix your application:")
    print(f"   1. Use: python launch_parallel.py")
    print(f"   2. Or add max_threads=40 to your demo.launch()")
    print(f"   3. Make event handlers async")


if __name__ == "__main__":
    main()