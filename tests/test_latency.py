"""
Test Latency - Run sample queries and generate performance report
Run this script to measure actual system latency
"""

import sys
import os
import time

# Add core to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'core'))

from core import ShoppingAssistantWorkflow
from core.performance import get_monitor, get_tracker
from analyze_latency import get_current_analysis, print_analysis_report, export_analysis

# Test queries of varying complexity
TEST_QUERIES = [
    {
        'name': 'Simple search',
        'query': 'Show me black bags under $100',
        'description': 'Simple query with basic filters'
    },
    {
        'name': 'Complex search',
        'query': 'I need a professional leather tote bag in brown or tan color, preferably with multiple compartments and suitable for work, budget around $200-400',
        'description': 'Complex query with multiple preferences'
    },
    {
        'name': 'Brand-specific search',
        'query': 'Show me Coach or Michael Kors crossbody bags',
        'description': 'Search with brand filter'
    }
]


def run_latency_test():
    """Run latency test with sample queries"""
    print("="*80)
    print("LATENCY TEST - Shopping Assistant")
    print("="*80)
    print("\nInitializing workflow...")
    
    try:
        # Initialize workflow
        workflow = ShoppingAssistantWorkflow()
        print("✓ Workflow initialized\n")
    except Exception as e:
        print(f"✗ Failed to initialize workflow: {e}")
        return
    
    monitor = get_monitor()
    results = []
    
    print("Running test queries...\n")
    
    for i, test in enumerate(TEST_QUERIES, 1):
        print(f"\n{'-'*80}")
        print(f"Test {i}/{len(TEST_QUERIES)}: {test['name']}")
        print(f"Query: {test['query']}")
        print(f"Description: {test['description']}")
        print('-'*80)
        
        try:
            # Start monitoring
            monitor.start_monitoring_request()
            
            # Run query
            session_id = f"test-session-{i}"
            final_state = workflow.process_query(test['query'], session_id)
            
            # Get timing results
            latency_breakdown = monitor.finish_monitoring_request(test['query'])
            
            # Get detailed analysis
            analysis = get_current_analysis()
            
            results.append({
                'test': test,
                'latency_breakdown': latency_breakdown,
                'analysis': analysis,
                'success': not final_state.get('error')
            })
            
            # Print summary
            total_time = latency_breakdown.get('total_ms', 0)
            rating = analysis.get('performance_rating', {})
            print(f"\n✓ Query completed in {total_time:.0f}ms - {rating.get('rating', 'unknown').upper()}")
            
            # Show top 3 bottlenecks
            bottlenecks = analysis.get('bottlenecks', [])
            if bottlenecks:
                print("\nTop bottlenecks:")
                for bottleneck in bottlenecks[:3]:
                    print(f"  - {bottleneck['operation']}: {bottleneck['duration_ms']:.0f}ms")
            
        except Exception as e:
            print(f"\n✗ Query failed: {e}")
            import traceback
            traceback.print_exc()
            results.append({
                'test': test,
                'error': str(e),
                'success': False
            })
        
        # Small delay between queries
        time.sleep(1)
    
    # Print overall summary
    print("\n\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    
    successful_tests = [r for r in results if r['success']]
    failed_tests = [r for r in results if not r['success']]
    
    print(f"\nTotal Tests: {len(TEST_QUERIES)}")
    print(f"Successful: {len(successful_tests)}")
    print(f"Failed: {len(failed_tests)}")
    
    if successful_tests:
        avg_time = sum(r['latency_breakdown']['total_ms'] for r in successful_tests) / len(successful_tests)
        min_time = min(r['latency_breakdown']['total_ms'] for r in successful_tests)
        max_time = max(r['latency_breakdown']['total_ms'] for r in successful_tests)
        
        print(f"\nLatency Statistics:")
        print(f"  Average: {avg_time:.0f}ms")
        print(f"  Min: {min_time:.0f}ms")
        print(f"  Max: {max_time:.0f}ms")
        
        # Rate overall performance
        if avg_time < 2000:
            print(f"\n✓ Overall performance: GOOD (avg < 2s)")
        elif avg_time < 3000:
            print(f"\n⚠ Overall performance: ACCEPTABLE (avg < 3s)")
        else:
            print(f"\n✗ Overall performance: NEEDS OPTIMIZATION (avg > 3s)")
    
    # Print detailed analysis for the last query
    if successful_tests:
        print("\n\n" + "="*80)
        print("DETAILED ANALYSIS (Last Query)")
        print("="*80)
        last_analysis = successful_tests[-1]['analysis']
        print_analysis_report(last_analysis)
        
        # Export detailed analysis
        export_analysis(last_analysis, 'latency_report_last_query.json')
    
    # Print aggregate statistics
    print("\n" + "="*80)
    print("AGGREGATE STATISTICS (All Operations)")
    print("="*80)
    print(get_tracker().get_summary())
    
    # Export aggregate data
    aggregate_stats = get_tracker().get_all_stats()
    with open('latency_aggregate_stats.json', 'w') as f:
        import json
        json.dump(aggregate_stats, f, indent=2)
    print("Aggregate stats exported to latency_aggregate_stats.json")
    
    print("\n" + "="*80)
    print("TEST COMPLETE")
    print("="*80)


if __name__ == '__main__':
    try:
        run_latency_test()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()
