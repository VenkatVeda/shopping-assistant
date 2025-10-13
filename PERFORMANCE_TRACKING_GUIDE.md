# Performance Tracking Implementation Guide

## Overview
Your Shopping Assistant now includes comprehensive performance monitoring with LangSmith-style token usage and latency information displayed both in the UI and server logs.

## Features Added

### 1. Token and Latency Tracking
- **Token Usage**: Tracks tokens consumed per LLM call
- **Latency Monitoring**: Measures response time for each request
- **Cost Tracking**: Calculates cost per API call (when available)
- **Cumulative Statistics**: Maintains running totals and averages

### 2. Enhanced Logging
Server logs now include detailed performance metrics:
```
🤖 [14:32:15] LLM Call | Tokens: 150 | Latency: 1.23s | Cost: $0.0045
📊 [14:32:15] Totals | Tokens: 1250 | Requests: 8 | Avg Latency: 1.45s
📝 [14:32:15] [USER_QUERY] Session: a1b2c3d4 | Type: chat_response | Query: Show me leather bags | Tokens: 150 | Latency: 1.23s | Cost: $0.0045
```

### 3. UI Performance Display
Each assistant response now shows performance metrics:
```
⚡ Tokens: 150 | ⏱️ 1.23s | 💰 $0.0045
```

## Technical Implementation

### Azure Service Enhancement
- `run_with_tracking()` method wraps all LLM calls
- Uses LangChain's `get_openai_callback()` for accurate token counting
- Maintains session-level performance statistics

### Session Manager Updates
- `log_user_query()` method enhanced to include metrics
- Performance data included in server monitoring logs
- Supports both basic queries and metrics-enhanced logging

### UI Integration
- Gradio interface displays metrics below each response
- Clean styling with monospace font and subtle colors
- Mobile-responsive design maintained

### Workflow Updates
- `process_message()` now returns tuple: (response, metrics)
- Metrics passed through entire processing pipeline
- Compatible with both sync and async processing

## Performance Statistics Available

### Per-Request Metrics
- `tokens`: Tokens used in this specific call
- `latency`: Response time in seconds
- `cost`: Estimated cost of this call

### Cumulative Metrics
- `total_tokens`: All tokens used in session
- `total_requests`: Number of LLM calls made
- `avg_latency`: Average response time
- `total_cost`: Running cost total

## Usage Examples

### Server Logs
Monitor performance in real-time through server console:
```bash
🤖 [14:32:15] LLM Call | Tokens: 150 | Latency: 1.23s | Cost: $0.0045
📊 [14:32:15] Totals | Tokens: 1250 | Requests: 8 | Avg Latency: 1.45s
```

### UI Display
Users see performance info below each response:
- Professional, non-intrusive display
- Helps users understand system performance
- Builds trust through transparency

### Programmatic Access
```python
# Get current performance statistics
stats = azure_service.get_performance_stats()
print(f"Total tokens: {stats['total_tokens']}")
print(f"Average latency: {stats['average_latency']:.2f}s")
```

## Testing

Run the performance tracking test:
```bash
python test_performance_tracking.py
```

This will validate:
- ✅ Service initialization
- ✅ Session management
- ✅ Metrics collection
- ✅ Enhanced logging
- ✅ UI integration readiness

## Deployment Ready

All performance tracking features are:
- ✅ Production-ready
- ✅ Render-compatible
- ✅ Redis-integrated
- ✅ Session-aware
- ✅ Mobile-responsive

The enhanced monitoring provides valuable insights into:
- User engagement patterns
- System performance trends
- Cost optimization opportunities
- Response time analysis

Your users will appreciate the transparency, and you'll have comprehensive analytics for monitoring and optimization!