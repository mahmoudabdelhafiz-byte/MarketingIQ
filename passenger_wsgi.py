from __future__ import annotations

from threading import Lock

from a2wsgi import ASGIMiddleware

from marketingiq.api.production import create_production_app

# cPanel/Passenger expects a WSGI callable named "application".
# Validate the production configuration at import time, but delay construction
# of the ASGI-to-WSGI adapter until a worker handles its first request. This
# avoids inheriting adapter event-loop state across Passenger's pre-fork model.
_asgi_app = create_production_app()
_wsgi_app = None
_wsgi_lock = Lock()


def application(environ, start_response):
    global _wsgi_app

    if _wsgi_app is None:
        with _wsgi_lock:
            if _wsgi_app is None:
                _wsgi_app = ASGIMiddleware(_asgi_app)

    return _wsgi_app(environ, start_response)
