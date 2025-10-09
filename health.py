# health.py - Health check endpoint for Docker container monitoring

import json
import time
from datetime import datetime
from typing import Dict, Any

class HealthChecker:
    """Health monitoring for the Shopping Assistant application"""
    
    def __init__(self, app_instance=None):
        self.app = app_instance
        self.start_time = time.time()
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get comprehensive health status"""
        status = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "uptime_seconds": int(time.time() - self.start_time),
            "services": {},
            "cache": {},
            "sessions": {}
        }
        
        try:
            if self.app:
                # Check Azure OpenAI service
                status["services"]["azure_openai"] = {
                    "status": "healthy" if self.app.azure_service.is_available() else "unhealthy",
                    "service": "Azure OpenAI API"
                }
                
                # Check vector database
                status["services"]["vector_db"] = {
                    "status": "healthy" if self.app.vector_service.is_available() else "unhealthy",
                    "service": "ChromaDB Vector Database"
                }
                
                # Check product data
                product_count = len(self.app.data_loader.url_to_image) if self.app.data_loader.url_to_image else 0
                status["services"]["product_data"] = {
                    "status": "healthy" if product_count > 0 else "unhealthy",
                    "count": product_count,
                    "service": "Product Catalog"
                }
                
                # Check cache system
                from main import _cache
                status["cache"] = {
                    "type": "redis" if _cache.use_redis else "memory",
                    "status": "healthy"
                }
                
                # Redis-specific health check
                if _cache.use_redis and _cache.redis_client:
                    try:
                        _cache.redis_client.ping()
                        status["cache"]["redis_ping"] = "success"
                    except:
                        status["cache"]["redis_ping"] = "failed"
                        status["cache"]["status"] = "degraded"
                
                # Check session manager
                if self.app.session_manager:
                    session_count = self.app.session_manager.get_session_count()
                    status["sessions"] = {
                        "active_count": session_count,
                        "status": "healthy"
                    }
                
                # Determine overall status
                service_statuses = [svc["status"] for svc in status["services"].values()]
                if any(s == "unhealthy" for s in service_statuses):
                    status["status"] = "unhealthy"
                elif any(s == "degraded" for s in [status["cache"]["status"]]):
                    status["status"] = "degraded"
            
        except Exception as e:
            status["status"] = "unhealthy"
            status["error"] = str(e)
        
        return status
    
    def is_healthy(self) -> bool:
        """Simple boolean health check"""
        health = self.get_health_status()
        return health["status"] in ["healthy", "degraded"]


# Global health checker instance
_health_checker = None

def get_health_checker(app=None):
    """Get or create health checker instance"""
    global _health_checker
    if _health_checker is None:
        _health_checker = HealthChecker(app)
    return _health_checker

def health_check_endpoint():
    """Health check endpoint for Docker/Kubernetes"""
    health = get_health_checker().get_health_status()
    return json.dumps(health, indent=2)

def simple_health_check():
    """Simple health check that returns 200 OK or 503 Service Unavailable"""
    is_healthy = get_health_checker().is_healthy()
    status_code = 200 if is_healthy else 503
    return status_code, "OK" if is_healthy else "Service Unavailable"


if __name__ == "__main__":
    # Standalone health check for testing
    print("Health Check Status:")
    print(health_check_endpoint())