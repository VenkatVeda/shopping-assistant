"""
Performance Monitoring and Latency Tracking for Shopping Assistant
Provides utilities to measure and analyze system latency across all components
"""

import time
import functools
import logging
from typing import Dict, List, Optional, Any, Callable
from contextlib import contextmanager
from collections import defaultdict
import json

logger = logging.getLogger(__name__)


class LatencyTracker:
    """Tracks latency metrics across the application"""
    
    def __init__(self):
        self.timings: Dict[str, List[float]] = defaultdict(list)
        self.current_request_timings: Dict[str, float] = {}
        self.request_start_time: Optional[float] = None
        
    def start_request(self):
        """Mark the start of a new request"""
        self.request_start_time = time.time()
        self.current_request_timings = {}
        
    def record(self, operation: str, duration: float):
        """Record timing for an operation"""
        self.timings[operation].append(duration)
        self.current_request_timings[operation] = duration
        
    def get_current_timings(self) -> Dict[str, float]:
        """Get timings for the current request"""
        if self.request_start_time:
            total_time = time.time() - self.request_start_time
            return {
                **self.current_request_timings,
                'total_request_time': total_time
            }
        return self.current_request_timings
    
    def get_stats(self, operation: str) -> Dict[str, float]:
        """Get statistics for a specific operation"""
        if operation not in self.timings or not self.timings[operation]:
            return {}
        
        timings = self.timings[operation]
        return {
            'count': len(timings),
            'avg': sum(timings) / len(timings),
            'min': min(timings),
            'max': max(timings),
            'total': sum(timings)
        }
    
    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all operations"""
        return {op: self.get_stats(op) for op in self.timings.keys()}
    
    def get_summary(self) -> str:
        """Get a formatted summary of all statistics"""
        stats = self.get_all_stats()
        if not stats:
            return "No timing data collected yet."
        
        lines = ["=== LATENCY SUMMARY ===\n"]
        
        # Sort by average time (slowest first)
        sorted_ops = sorted(stats.items(), key=lambda x: x[1].get('avg', 0), reverse=True)
        
        for operation, stat in sorted_ops:
            lines.append(f"{operation}:")
            lines.append(f"  Count: {stat['count']}")
            lines.append(f"  Avg:   {stat['avg']*1000:.2f}ms")
            lines.append(f"  Min:   {stat['min']*1000:.2f}ms")
            lines.append(f"  Max:   {stat['max']*1000:.2f}ms")
            lines.append(f"  Total: {stat['total']*1000:.2f}ms")
            lines.append("")
        
        return "\n".join(lines)
    
    def export_json(self) -> str:
        """Export all statistics as JSON"""
        return json.dumps(self.get_all_stats(), indent=2)
    
    def reset(self):
        """Clear all recorded timings"""
        self.timings.clear()
        self.current_request_timings.clear()
        self.request_start_time = None


# Global tracker instance
_global_tracker = LatencyTracker()


def get_tracker() -> LatencyTracker:
    """Get the global latency tracker"""
    return _global_tracker


@contextmanager
def track_time(operation: str, tracker: Optional[LatencyTracker] = None):
    """
    Context manager to track execution time
    
    Usage:
        with track_time("embedding_generation"):
            embeddings = generate_embeddings(text)
    """
    if tracker is None:
        tracker = _global_tracker
    
    start_time = time.time()
    try:
        yield
    finally:
        duration = time.time() - start_time
        tracker.record(operation, duration)
        logger.info(f"⏱️ {operation}: {duration*1000:.2f}ms")


def time_function(operation: Optional[str] = None, tracker: Optional[LatencyTracker] = None):
    """
    Decorator to track function execution time
    
    Usage:
        @time_function("process_query")
        def process_query(query: str):
            ...
    """
    def decorator(func: Callable) -> Callable:
        op_name = operation or f"{func.__module__}.{func.__name__}"
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            t = tracker or _global_tracker
            with track_time(op_name, t):
                return func(*args, **kwargs)
        
        return wrapper
    return decorator


def log_request_timings(timings: Dict[str, float], query: str = ""):
    """
    Log detailed request timings in a structured format
    """
    total = timings.get('total_request_time', 0)
    
    logger.info("=" * 80)
    logger.info(f"🔍 Query: {query[:100]}")
    logger.info(f"⏱️  TOTAL REQUEST TIME: {total*1000:.2f}ms ({total:.3f}s)")
    logger.info("-" * 80)
    
    # Sort by duration (slowest first)
    sorted_timings = sorted(
        [(k, v) for k, v in timings.items() if k != 'total_request_time'],
        key=lambda x: x[1],
        reverse=True
    )
    
    for operation, duration in sorted_timings:
        percentage = (duration / total * 100) if total > 0 else 0
        logger.info(f"  {operation:40s}: {duration*1000:8.2f}ms ({percentage:5.1f}%)")
    
    logger.info("=" * 80)


def identify_bottlenecks(timings: Dict[str, float], threshold_ms: float = 500) -> List[str]:
    """
    Identify operations that are taking too long
    
    Args:
        timings: Dictionary of operation names to durations
        threshold_ms: Threshold in milliseconds to flag as bottleneck
        
    Returns:
        List of bottleneck operation names
    """
    bottlenecks = []
    threshold_s = threshold_ms / 1000
    
    for operation, duration in timings.items():
        if operation != 'total_request_time' and duration > threshold_s:
            bottlenecks.append(f"{operation} ({duration*1000:.2f}ms)")
    
    return bottlenecks


def format_latency_breakdown(timings: Dict[str, float]) -> Dict[str, Any]:
    """
    Format latency breakdown for API response
    
    Returns a structured breakdown of where time was spent
    """
    total = timings.get('total_request_time', 0)
    
    breakdown = {
        'total_ms': round(total * 1000, 2),
        'total_seconds': round(total, 3),
        'operations': []
    }
    
    # Sort by duration
    sorted_timings = sorted(
        [(k, v) for k, v in timings.items() if k != 'total_request_time'],
        key=lambda x: x[1],
        reverse=True
    )
    
    for operation, duration in sorted_timings:
        percentage = (duration / total * 100) if total > 0 else 0
        breakdown['operations'].append({
            'name': operation,
            'duration_ms': round(duration * 1000, 2),
            'duration_seconds': round(duration, 3),
            'percentage': round(percentage, 1)
        })
    
    # Identify bottlenecks
    breakdown['bottlenecks'] = identify_bottlenecks(timings, threshold_ms=500)
    
    return breakdown


class PerformanceMonitor:
    """
    High-level performance monitoring for the entire request lifecycle
    """
    
    def __init__(self):
        self.tracker = LatencyTracker()
        self.request_count = 0
        self.slow_request_count = 0
        self.slow_request_threshold = 3.0  # 3 seconds
        
    def start_monitoring_request(self):
        """Start monitoring a new request"""
        self.tracker.start_request()
        self.request_count += 1
        
    def finish_monitoring_request(self, query: str = "") -> Dict[str, Any]:
        """Finish monitoring and return summary"""
        timings = self.tracker.get_current_timings()
        
        # Check if this was a slow request
        total_time = timings.get('total_request_time', 0)
        if total_time > self.slow_request_threshold:
            self.slow_request_count += 1
            logger.warning(f"⚠️ SLOW REQUEST detected: {total_time:.2f}s")
        
        # Log detailed timings
        log_request_timings(timings, query)
        
        # Return formatted breakdown
        return format_latency_breakdown(timings)
    
    def get_overall_stats(self) -> Dict[str, Any]:
        """Get overall statistics across all requests"""
        from datetime import datetime
        return {
            'total_requests': self.request_count,
            'slow_requests': self.slow_request_count,
            'slow_request_threshold_seconds': self.slow_request_threshold,
            'operation_stats': self.tracker.get_all_stats(),
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }


# Global monitor instance
_global_monitor = PerformanceMonitor()


def get_monitor() -> PerformanceMonitor:
    """Get the global performance monitor"""
    return _global_monitor
