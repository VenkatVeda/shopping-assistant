#!/usr/bin/env python3
"""
Test True Parallel Processing

This script demonstrates that the new implementation truly processes requests
in parallel without FIFO queuing.
"""

import asyncio
import aiohttp
import time
import json
from concurrent.futures import ThreadPoolExecutor
import threading


async def send_request(session, url, user_input, user_id):
    """Send a request to the Gradio API"""
    start_time = time.time()
    thread_name = threading.current_thread().name
    
    print(f"🚀 [{thread_name}] User {user_id} sending: '{user_input}' at {time.strftime('%H:%M:%S.%f')[:-3]}")
    
    # Prepare the request data for Gradio API
    data = {
        "data": [user_input, None],  # [message, session_id]
        "fn_index": 0  # Usually the first function (send message)
    }
    
    try:
        async with session.post(f"{url}/api/predict", json=data) as response:
            result = await response.json()
            end_time = time.time()
            processing_time = end_time - start_time
            
            print(f"✅ [{thread_name}] User {user_id} completed in {processing_time:.2f}s at {time.strftime('%H:%M:%S.%f')[:-3]}")
            return {
                'user_id': user_id,
                'input': user_input,
                'processing_time': processing_time,
                'start_time': start_time,
                'end_time': end_time,
                'thread': thread_name,
                'success': response.status == 200
            }
    except Exception as e:
        end_time = time.time()
        processing_time = end_time - start_time
        print(f"❌ [{thread_name}] User {user_id} failed after {processing_time:.2f}s: {e}")
        return {
            'user_id': user_id,
            'input': user_input,
            'processing_time': processing_time,
            'start_time': start_time,
            'end_time': end_time,
            'thread': thread_name,
            'success': False,
            'error': str(e)
        }


async def test_parallel_processing(base_url="http://localhost:7860", num_users=5):
    """Test parallel processing with multiple simultaneous requests"""
    
    # Test queries that each user will send
    test_queries = [
        "Show me leather crossbody bags under $100",
        "I need tote bags for work",
        "Find designer handbags on sale",
        "Show me backpacks for travel",
        "I want clutch bags for evening events"
    ]
    
    print(f"🧪 Testing parallel processing with {num_users} users")
    print(f"📍 Target URL: {base_url}")
    print(f"⏰ Test started at: {time.strftime('%H:%M:%S')}")
    print("-" * 60)
    
    # Create HTTP session
    async with aiohttp.ClientSession() as session:
        # Create tasks for all users to send requests simultaneously
        tasks = []
        for i in range(num_users):
            user_input = test_queries[i % len(test_queries)]
            task = send_request(session, base_url, user_input, i + 1)
            tasks.append(task)
        
        # Execute all requests concurrently
        test_start = time.time()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        test_end = time.time()
        total_time = test_end - test_start
        
        print("-" * 60)
        print(f"⏱️  Total test time: {total_time:.2f} seconds")
        print(f"📊 Results Summary:")
        
        successful_results = [r for r in results if isinstance(r, dict) and r.get('success', False)]
        failed_results = [r for r in results if isinstance(r, dict) and not r.get('success', False)]
        
        print(f"   ✅ Successful requests: {len(successful_results)}")
        print(f"   ❌ Failed requests: {len(failed_results)}")
        
        if successful_results:
            avg_processing_time = sum(r['processing_time'] for r in successful_results) / len(successful_results)
            min_processing_time = min(r['processing_time'] for r in successful_results)
            max_processing_time = max(r['processing_time'] for r in successful_results)
            
            print(f"   📈 Average processing time: {avg_processing_time:.2f}s")
            print(f"   ⚡ Fastest request: {min_processing_time:.2f}s")
            print(f"   🐌 Slowest request: {max_processing_time:.2f}s")
            
            # Check for parallel processing (overlap in processing times)
            overlaps = 0
            for i, result1 in enumerate(successful_results):
                for j, result2 in enumerate(successful_results):
                    if i != j:
                        # Check if processing times overlap (indicating parallel processing)
                        if (result1['start_time'] < result2['end_time'] and 
                            result2['start_time'] < result1['end_time']):
                            overlaps += 1
            
            overlap_ratio = overlaps / (len(successful_results) * (len(successful_results) - 1)) if len(successful_results) > 1 else 0
            
            print(f"   🔄 Processing overlap ratio: {overlap_ratio:.2%}")
            if overlap_ratio > 0.3:
                print("   🎉 TRUE PARALLEL PROCESSING DETECTED! ✅")
            else:
                print("   ⚠️  Limited parallel processing detected (may still be FIFO)")
        
        # Detailed timeline
        if successful_results:
            print("\n📅 Processing Timeline:")
            for result in sorted(successful_results, key=lambda x: x['start_time']):
                start_str = time.strftime('%H:%M:%S.%f', time.localtime(result['start_time']))[:-3]
                end_str = time.strftime('%H:%M:%S.%f', time.localtime(result['end_time']))[:-3]
                print(f"   User {result['user_id']}: {start_str} → {end_str} ({result['processing_time']:.2f}s)")


async def simple_health_check(base_url="http://localhost:7860"):
    """Simple health check to see if the server is running"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{base_url}/") as response:
                if response.status == 200:
                    print(f"✅ Server is running at {base_url}")
                    return True
                else:
                    print(f"⚠️  Server responded with status {response.status}")
                    return False
    except Exception as e:
        print(f"❌ Cannot connect to server at {base_url}: {e}")
        return False


def main():
    """Main test runner"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Test True Parallel Processing")
    parser.add_argument("--url", default="http://localhost:7860", help="Base URL for the application")
    parser.add_argument("--users", type=int, default=5, help="Number of concurrent users to simulate")
    parser.add_argument("--check-only", action="store_true", help="Only check if server is running")
    
    args = parser.parse_args()
    
    if args.check_only:
        print("🔍 Performing health check...")
        result = asyncio.run(simple_health_check(args.url))
        if result:
            print("✅ Health check passed!")
        else:
            print("❌ Health check failed!")
        return
    
    print("🧪 True Parallel Processing Test")
    print("=" * 50)
    
    # First check if server is running
    print("🔍 Checking server availability...")
    server_ok = asyncio.run(simple_health_check(args.url))
    
    if not server_ok:
        print("\n❌ Server is not running! Please start the application first:")
        print("   python launch_true_parallel.py")
        return
    
    print("\n🚀 Starting parallel processing test...")
    asyncio.run(test_parallel_processing(args.url, args.users))
    
    print("\n📝 Test completed!")
    print("\n💡 How to interpret results:")
    print("   • If overlap ratio > 30%, parallel processing is working")
    print("   • If processing times overlap in timeline, requests are concurrent")
    print("   • If times are sequential, there's still FIFO queuing")


if __name__ == "__main__":
    main()