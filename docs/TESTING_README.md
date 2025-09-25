# Conversational Flow Testing Framework

## Overview
This testing framework provides comprehensive validation of the Shopping Assistant's conversational capabilities through 15 distinct user sessions with 105+ individual conversations.

## Quick Start

### 1. Validate Environment
```bash
python validate_test_environment.py
```

### 2. Run Tests
```bash
# Full test suite (15 sessions, 105+ conversations)
python run_conversational_tests.py --full

# Quick test (first 5 sessions)
python run_conversational_tests.py --quick

# Specific session
python run_conversational_tests.py --session "New User Basic Search"

# List all available sessions
python run_conversational_tests.py --list
```

## Test Sessions

| Session | Description | Conversations | Focus Area |
|---------|-------------|---------------|------------|
| 1. New User Basic Search | First-time user exploration | 6 | Basic functionality |
| 2. Budget Shopper | Price-conscious customer | 6 | Price filtering |
| 3. Luxury Shopper | High-end customer | 7 | Premium products |
| 4. Brand Loyal Customer | Brand-focused user | 6 | Brand preferences |
| 5. Style-Focused Shopper | Fashion-conscious user | 7 | Style & aesthetics |
| 6. Practical Shopper | Utility-focused customer | 7 | Functionality |
| 7. Gift Purchaser | Buying gifts for others | 7 | Gift scenarios |
| 8. Comparison Shopper | Thorough comparison user | 7 | Product comparison |
| 9. Seasonal Buyer | Seasonal shopping | 7 | Seasonal products |
| 10. Problem-Solving Customer | Issue resolution | 7 | Problem solving |
| 11. Fashion Trendsetter | Trend-focused user | 7 | Latest trends |
| 12. Detail-Oriented Buyer | Specification seeker | 7 | Technical details |
| 13. Indecisive Customer | Decision difficulty | 7 | Guidance needs |
| 14. Returning Customer | Repeat customer | 7 | Customer retention |
| 15. Complex Requirements | Multi-constraint user | 8 | Complex filtering |

## Output Files

### Console Output
- Real-time progress updates
- Individual conversation results
- Session summaries
- Overall statistics

### Generated Reports
- `conversational_flow_test_report_YYYYMMDD_HHMMSS.json` - Detailed JSON report

## Success Metrics
- **95-100%**: Excellent performance
- **85-94%**: Good performance 
- **70-84%**: Fair performance (needs improvement)
- **<70%**: Poor performance (requires investigation)

## Troubleshooting

### Common Issues
1. **App initialization failure**: Check Azure credentials and dependencies
2. **Low success rates**: Review expected keywords and system responses
3. **Performance issues**: Monitor system resources and network connectivity
4. **Import errors**: Ensure all dependencies are installed

### Prerequisites
- Python 3.8+
- All project dependencies installed
- Azure OpenAI credentials configured
- Virtual environment activated

## Documentation
See `CONVERSATIONAL_FLOW_TESTING_DOCUMENTATION.txt` for comprehensive details.

## Files
- `test_conversational_flow.py` - Main testing framework
- `run_conversational_tests.py` - Test runner with options
- `validate_test_environment.py` - Environment validation
- `CONVERSATIONAL_FLOW_TESTING_DOCUMENTATION.txt` - Complete documentation