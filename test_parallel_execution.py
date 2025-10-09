# test_parallel_execution.py
"""
Test script to demonstrate the difference between synchronous and parallel execution
"""

import time
import threading
from concurrent.futures import ThreadPoolExecutor
from main import ShoppingAssistantApp


def test_synchronous_execution():
    """Test how the current system behaves with multiple users (FIFO)"""
    print("🔍 Testing SYNCHRONOUS execution (current behavior)...")
    
    app = ShoppingAssistantApp()
    
    def simulate_user_sync(user_id):
        """Simulate a user in synchronous mode"""
        start_time = time.time()
        print(f"👤 SYNC User {user_id} starting at {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}")
        
        # Create session
        session_id, session_data = app.session_manager.get_or_create_session()
        
        # Send a message that requires processing
        message = f"I'm user {user_id} looking for leather bags under $100"
        result = session_data.workflow.process_message(message, session_id)
        
        end_time = time.time()
        processing_time = end_time - start_time
        print(f"✅ SYNC User {user_id} completed at {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} (took {processing_time:.2f}s)")
        
        return user_id, processing_time, start_time, end_time
    
    # Test with 3 users sequentially (this simulates Gradio's current behavior)
    start_time = time.time()
    print(f"\n🏃‍♂️ Starting 3 users SEQUENTIALLY at {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}")
    
    results = []
    for i in range(3):
        result = simulate_user_sync(i + 1)
        results.append(result)
    
    total_time = time.time() - start_time
    
    print(f"\n📊 SYNCHRONOUS Results:")
    print(f"   • Total time: {total_time:.2f}s")
    print(f"   • Average user time: {sum(r[1] for r in results) / len(results):.2f}s")
    print(f"   • User 1 start-to-end: {results[0][1]:.2f}s")
    print(f"   • User 2 start-to-end: {results[1][1]:.2f}s") 
    print(f"   • User 3 start-to-end: {results[2][1]:.2f}s")
    print(f"   • Behavior: FIFO (User 2 waits for User 1, User 3 waits for User 2)")
    
    return results, total_time


def test_parallel_execution():
    """Test parallel execution with enhanced session management"""
    print("\n🚀 Testing PARALLEL execution (enhanced behavior)...")
    
    app = ShoppingAssistantApp()
    
    def simulate_user_parallel(user_id):
        """Simulate a user in parallel mode"""
        start_time = time.time()
        print(f"👤 PARALLEL User {user_id} starting at {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}")
        
        # Create session
        session_id, session_data = app.session_manager.get_or_create_session()
        
        # Send a message that requires processing
        message = f"I'm user {user_id} looking for crossbody bags under $150"
        result = session_data.workflow.process_message(message, session_id)
        
        end_time = time.time()
        processing_time = end_time - start_time
        print(f"✅ PARALLEL User {user_id} completed at {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} (took {processing_time:.2f}s)")
        
        return user_id, processing_time, start_time, end_time
    
    # Test with 3 users concurrently
    start_time = time.time()
    print(f"\n🏃‍♂️ Starting 3 users CONCURRENTLY at {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}")
    
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(simulate_user_parallel, i + 1) for i in range(3)]
        results = [future.result() for future in futures]
    
    total_time = time.time() - start_time
    
    print(f"\n📊 PARALLEL Results:")
    print(f"   • Total time: {total_time:.2f}s")
    print(f"   • Average user time: {sum(r[1] for r in results) / len(results):.2f}s")
    print(f"   • User 1 start-to-end: {results[0][1]:.2f}s")
    print(f"   • User 2 start-to-end: {results[1][1]:.2f}s")
    print(f"   • User 3 start-to-end: {results[2][1]:.2f}s")
    print(f"   • Behavior: CONCURRENT (All users process simultaneously)")
    
    return results, total_time


def test_gradio_behavior_simulation():
    """Simulate how Gradio currently handles multiple requests"""
    print("\n🎭 Simulating GRADIO BEHAVIOR...")
    
    from ui.gradio_interface import GradioInterface
    from main import ShoppingAssistantApp
    
    app = ShoppingAssistantApp()
    ui = GradioInterface(app.session_manager)
    
    def simulate_gradio_request(user_id):
        """Simulate a Gradio request (this shows the FIFO behavior)"""
        start_time = time.time()
        print(f"🌐 Gradio User {user_id} request at {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}")
        
        # This simulates what happens when user clicks send button
        user_input = f"I'm user {user_id} looking for designer bags"
        chat_history, session_id = ui.chat_interface(user_input, None)
        
        end_time = time.time()
        processing_time = end_time - start_time
        print(f"✅ Gradio User {user_id} response at {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d} (took {processing_time:.2f}s)")
        
        return user_id, processing_time
    
    # Test how Gradio processes requests (this will be sequential due to Gradio's design)
    start_time = time.time()
    print(f"\n🔄 Testing Gradio request handling at {time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}")
    
    # This simulates what actually happens in Gradio - requests are processed one by one
    results = []
    for i in range(3):
        result = simulate_gradio_request(i + 1)
        results.append(result)
    
    total_time = time.time() - start_time
    
    print(f"\n📊 GRADIO SIMULATION Results:")
    print(f"   • Total time: {total_time:.2f}s")
    print(f"   • Average request time: {sum(r[1] for r in results) / len(results):.2f}s")
    print(f"   • Behavior: SEQUENTIAL (Gradio processes one request at a time)")
    print(f"   • Problem: User 2 and 3 wait for previous users to complete")
    
    return results, total_time


def main():
    """Run all tests to demonstrate the problem and solution"""
    print("=" * 80)
    print("🧪 PARALLEL EXECUTION TEST SUITE")
    print("=" * 80)
    
    # Test 1: Current synchronous behavior
    sync_results, sync_time = test_synchronous_execution()
    
    # Test 2: Enhanced parallel behavior  
    parallel_results, parallel_time = test_parallel_execution()
    
    # Test 3: Gradio behavior simulation
    gradio_results, gradio_time = test_gradio_behavior_simulation()
    
    # Compare results
    print("\n" + "=" * 80)
    print("📊 COMPARISON SUMMARY")
    print("=" * 80)
    
    print(f"\n🔍 Current System (FIFO Problem):")
    print(f"   • Sequential processing time: {sync_time:.2f}s")
    print(f"   • Gradio simulation time: {gradio_time:.2f}s")
    print(f"   • User experience: BAD (users wait for each other)")
    
    print(f"\n🚀 Enhanced System (Parallel Solution):")
    print(f"   • Concurrent processing time: {parallel_time:.2f}s")
    print(f"   • Speedup: {sync_time/parallel_time:.1f}x faster")
    print(f"   • User experience: GOOD (users process simultaneously)")
    
    improvement = ((sync_time - parallel_time) / sync_time) * 100
    print(f"\n✨ Performance Improvement: {improvement:.1f}% faster")
    
    print(f"\n🎯 Solution Summary:")
    print(f"   • Enable Gradio max_threads parameter")
    print(f"   • Use async event handlers") 
    print(f"   • Run blocking operations in thread pools")
    print(f"   • Maintain session isolation")
    
    print("\n🚀 To enable parallel execution:")
    print("   python launch_parallel.py")


if __name__ == "__main__":
    main()