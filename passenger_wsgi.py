from __future__ import annotations

from a2wsgi import ASGIMiddleware

from marketingiq.api.production import create_production_app

# cPanel/Passenger expects a WSGI callable named "application".
# The guarded production factory remains authoritative; invalid production
# configuration therefore fails before Passenger starts serving requests.
application = ASGIMiddleware(create_production_app())
