from __future__ import annotations

from threading import Lock

from a2wsgi import ASGIMiddleware

from marketingiq.api.production import create_production_app


# Validate production configuration when Passenger loads the application,
# but delay construction of the ASGI->WSGI event-loop adapter until the
# worker handles its first request.
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