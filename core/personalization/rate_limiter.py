"""
Simple rate limiter for LLM calls.
"""

import time
from typing import Dict, List
from collections import defaultdict


class RateLimiter:
    """
    Token bucket rate limiter for API calls.
    """
    
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        """
        Args:
            max_requests: Maximum requests allowed per window
            window_seconds: Time window in seconds
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.user_requests: Dict[str, List[float]] = defaultdict(list)
    
    def is_allowed(self, user_id: str) -> bool:
        """Check if user is allowed to make a request."""
        now = time.time()
        active = [t for t in self.user_requests[user_id] if now - t < self.window_seconds]

        if not active:
            # Prune the key entirely to prevent unbounded dict growth over the
            # process lifetime (one entry per ever-seen user_id)
            if user_id in self.user_requests:
                del self.user_requests[user_id]

        if len(active) >= self.max_requests:
            return False

        active.append(now)
        self.user_requests[user_id] = active
        return True

    def get_remaining(self, user_id: str) -> int:
        """Get remaining requests for user within the current window."""
        now    = time.time()
        active = [t for t in self.user_requests.get(user_id, []) if now - t < self.window_seconds]
        return max(0, self.max_requests - len(active))

    def reset(self, user_id: str):
        """Reset rate limit for user."""
        self.user_requests.pop(user_id, None)
