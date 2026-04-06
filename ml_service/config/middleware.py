import time

from django.urls import resolve, Resolver404
from .metrics import HTTP_REQUESTS_TOTAL, HTTP_REQUEST_DURATION


class PrometheusMiddleware:
    """
    Django middleware that records HTTP request metrics.
    Measures total request time including all other middleware.

    Paths are normalized to avoid high cardinality,
    e.g., /api/clusters/1234567890/ becomes /api/clusters/\<guild_id\>/.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        duration = time.monotonic() - start

        path = self._normalize_path(request)
        method = request.method
        status = str(response.status_code)

        HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status=status).inc()
        HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration)

        return response
    
    def _normalize_path(self, request) -> str:
        """
        Replaces dynamic path segments with placeholder labels.
        """

        try:
            match = resolve(request.path.info)
            if match.url_name:
                return f"/{match.url_name}/"
            
            return request.path_info
        except Resolver404:
            return "/unknown/" # using a placeholder when path is unknown to avoid cardinality explosion
