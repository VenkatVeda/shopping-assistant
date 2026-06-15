"""
Latency Analysis and Optimization Guide
Comprehensive script to analyze performance bottlenecks and provide optimization recommendations
"""

import json
import sys
import os
from typing import Dict, List, Any

# Add core module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'core'))
from core.performance import get_monitor, get_tracker


def analyze_latency_breakdown(timings: Dict[str, float]) -> Dict[str, Any]:
    """
    Analyze latency breakdown and identify bottlenecks
    
    Returns:
        Dictionary with analysis results and recommendations
    """
    total_time = timings.get('total_request_time', 0)
    
    if total_time == 0:
        return {
            'status': 'no_data',
            'message': 'No timing data available. Make sure to run some queries first.'
        }
    
    # Categorize operations
    categories = {
        'LLM Calls': [],
        'Vector Search': [],
        'Workflow Nodes': [],
        'API Processing': [],
        'Other': []
    }
    
    for operation, duration in timings.items():
        if operation == 'total_request_time':
            continue
            
        percentage = (duration / total_time * 100) if total_time > 0 else 0
        
        op_info = {
            'name': operation,
            'duration_ms': duration * 1000,
            'percentage': percentage
        }
        
        # Categorize
        if 'llm' in operation.lower() or 'intent_classification' in operation:
            categories['LLM Calls'].append(op_info)
        elif 'pinecone' in operation or 'embedding' in operation:
            categories['Vector Search'].append(op_info)
        elif 'node_' in operation:
            categories['Workflow Nodes'].append(op_info)
        elif 'api_' in operation:
            categories['API Processing'].append(op_info)
        else:
            categories['Other'].append(op_info)
    
    # Calculate category totals
    category_totals = {}
    for category, ops in categories.items():
        if ops:
            total = sum(op['duration_ms'] for op in ops)
            category_totals[category] = {
                'total_ms': total,
                'percentage': (total / (total_time * 1000)) * 100,
                'operations': ops
            }
    
    # Identify bottlenecks (> 500ms or > 30% of total)
    bottlenecks = []
    for operation, duration in timings.items():
        if operation == 'total_request_time':
            continue
        
        percentage = (duration / total_time * 100) if total_time > 0 else 0
        duration_ms = duration * 1000
        
        if duration_ms > 500 or percentage > 30:
            bottlenecks.append({
                'operation': operation,
                'duration_ms': duration_ms,
                'percentage': percentage,
                'severity': 'high' if duration_ms > 1000 else 'medium'
            })
    
    # Generate recommendations
    recommendations = generate_recommendations(category_totals, bottlenecks, total_time)
    
    return {
        'status': 'success',
        'total_time_ms': total_time * 1000,
        'total_time_seconds': total_time,
        'category_breakdown': category_totals,
        'bottlenecks': sorted(bottlenecks, key=lambda x: x['duration_ms'], reverse=True),
        'recommendations': recommendations,
        'performance_rating': rate_performance(total_time)
    }


def generate_recommendations(category_totals: Dict, bottlenecks: List[Dict], total_time: float) -> List[Dict]:
    """Generate specific optimization recommendations"""
    recommendations = []
    
    # Check overall performance
    total_ms = total_time * 1000
    if total_ms > 3000:
        recommendations.append({
            'priority': 'high',
            'category': 'overall',
            'issue': f'Total response time is {total_ms:.0f}ms (>3s threshold)',
            'recommendation': 'Consider enabling caching, reducing LLM token limits, or optimizing workflow complexity'
        })
    
    # Check LLM calls
    llm_category = category_totals.get('LLM Calls', {})
    if llm_category and llm_category['percentage'] > 50:
        recommendations.append({
            'priority': 'high',
            'category': 'llm',
            'issue': f'LLM calls account for {llm_category["percentage"]:.1f}% of total time',
            'recommendation': 'Optimize by: 1) Reduce max_tokens in LLM config, 2) Use faster models for non-critical tasks, 3) Implement response caching for common queries'
        })
    
    # Check vector search
    vector_category = category_totals.get('Vector Search', {})
    if vector_category and vector_category['total_ms'] > 1000:
        recommendations.append({
            'priority': 'medium',
            'category': 'vector_search',
            'issue': f'Vector search taking {vector_category["total_ms"]:.0f}ms',
            'recommendation': 'Optimize by: 1) Reduce top_k parameter, 2) Add more restrictive filters, 3) Consider Pinecone performance tier upgrade'
        })
    
    # Check for multiple LLM calls
    llm_ops = llm_category.get('operations', [])
    if len(llm_ops) > 3:
        recommendations.append({
            'priority': 'medium',
            'category': 'llm',
            'issue': f'Making {len(llm_ops)} separate LLM calls per request',
            'recommendation': 'Consider combining prompts or using streaming responses to reduce latency perception'
        })
    
    # Check specific bottlenecks
    for bottleneck in bottlenecks:
        if 'embedding' in bottleneck['operation']:
            recommendations.append({
                'priority': 'medium',
                'category': 'embedding',
                'issue': f'Embedding generation taking {bottleneck["duration_ms"]:.0f}ms',
                'recommendation': 'Consider: 1) Batch embedding requests, 2) Use a faster embedding model, 3) Cache embeddings for common queries'
            })
        
        if 'reranking' in bottleneck['operation']:
            recommendations.append({
                'priority': 'low',
                'category': 'reranking',
                'issue': f'Reranking taking {bottleneck["duration_ms"]:.0f}ms',
                'recommendation': 'Reranking can be skipped for queries with <5 results or made optional'
            })
    
    return recommendations


def rate_performance(total_time: float) -> Dict[str, Any]:
    """Rate the overall performance"""
    total_ms = total_time * 1000
    
    if total_ms < 1000:
        return {'rating': 'excellent', 'color': 'green', 'message': 'Response time is excellent (< 1s)'}
    elif total_ms < 2000:
        return {'rating': 'good', 'color': 'blue', 'message': 'Response time is good (< 2s)'}
    elif total_ms < 3000:
        return {'rating': 'acceptable', 'color': 'yellow', 'message': 'Response time is acceptable (< 3s)'}
    elif total_ms < 5000:
        return {'rating': 'slow', 'color': 'orange', 'message': 'Response time is slow (< 5s) - optimization recommended'}
    else:
        return {'rating': 'very_slow', 'color': 'red', 'message': 'Response time is very slow (> 5s) - urgent optimization needed'}


def print_analysis_report(analysis: Dict[str, Any]):
    """Print a formatted analysis report"""
    print("\n" + "="*80)
    print("LATENCY ANALYSIS REPORT")
    print("="*80 + "\n")
    
    if analysis['status'] == 'no_data':
        print(analysis['message'])
        return
    
    # Overall performance
    rating = analysis['performance_rating']
    print(f"Overall Performance: {rating['rating'].upper()}")
    print(f"Total Time: {analysis['total_time_ms']:.2f}ms ({analysis['total_time_seconds']:.3f}s)")
    print(f"Status: {rating['message']}\n")
    
    # Category breakdown
    print("-"*80)
    print("TIME BREAKDOWN BY CATEGORY")
    print("-"*80)
    for category, data in sorted(analysis['category_breakdown'].items(), 
                                   key=lambda x: x[1]['total_ms'], reverse=True):
        print(f"\n{category}:")
        print(f"  Total: {data['total_ms']:.2f}ms ({data['percentage']:.1f}%)")
        print(f"  Operations:")
        for op in sorted(data['operations'], key=lambda x: x['duration_ms'], reverse=True):
            print(f"    - {op['name']:40s}: {op['duration_ms']:8.2f}ms ({op['percentage']:5.1f}%)")
    
    # Bottlenecks
    if analysis['bottlenecks']:
        print("\n" + "-"*80)
        print("IDENTIFIED BOTTLENECKS (>500ms or >30%)")
        print("-"*80)
        for bottleneck in analysis['bottlenecks']:
            severity_marker = "🔴" if bottleneck['severity'] == 'high' else "🟡"
            print(f"{severity_marker} {bottleneck['operation']:40s}: {bottleneck['duration_ms']:8.2f}ms ({bottleneck['percentage']:5.1f}%)")
    
    # Recommendations
    if analysis['recommendations']:
        print("\n" + "-"*80)
        print("OPTIMIZATION RECOMMENDATIONS")
        print("-"*80)
        for i, rec in enumerate(analysis['recommendations'], 1):
            priority_marker = "🔴" if rec['priority'] == 'high' else "🟡" if rec['priority'] == 'medium' else "🔵"
            print(f"\n{i}. {priority_marker} [{rec['priority'].upper()}] {rec['category'].upper()}")
            print(f"   Issue: {rec['issue']}")
            print(f"   Recommendation: {rec['recommendation']}")
    
    print("\n" + "="*80 + "\n")


def export_analysis(analysis: Dict[str, Any], filename: str = "latency_analysis.json"):
    """Export analysis to JSON file"""
    with open(filename, 'w') as f:
        json.dump(analysis, f, indent=2)
    print(f"Analysis exported to {filename}")


def get_current_analysis() -> Dict[str, Any]:
    """Get analysis of the most recent request"""
    tracker = get_tracker()
    timings = tracker.get_current_timings()
    
    if not timings:
        return {
            'status': 'no_data',
            'message': 'No timing data available. Make sure to run a query first.'
        }
    
    return analyze_latency_breakdown(timings)


def get_aggregate_analysis() -> Dict[str, Any]:
    """Get analysis across all tracked requests"""
    tracker = get_tracker()
    all_stats = tracker.get_all_stats()
    
    if not all_stats:
        return {
            'status': 'no_data',
            'message': 'No timing data available.'
        }
    
    # Calculate averages
    avg_timings = {op: stats['avg'] for op, stats in all_stats.items()}
    
    # Add aggregate info
    analysis = analyze_latency_breakdown(avg_timings)
    if analysis['status'] == 'success':
        analysis['aggregate_info'] = {
            'total_requests': all_stats.get('workflow_execution', {}).get('count', 0),
            'operation_counts': {op: stats['count'] for op, stats in all_stats.items()}
        }
    
    return analysis


if __name__ == '__main__':
    print("Latency Analysis Tool")
    print("=" * 80)
    print("\nTo use this tool:")
    print("1. Run some queries through your application")
    print("2. Import this module and call get_current_analysis() or get_aggregate_analysis()")
    print("\nExample:")
    print("  from analyze_latency import get_current_analysis, print_analysis_report")
    print("  analysis = get_current_analysis()")
    print("  print_analysis_report(analysis)")
    print("\n" + "=" * 80)
