def application(environ, start_response):
    body = b"MarketingIQ WSGI OK\n"
    start_response(
        "200 OK",
        [
            ("Content-Type", "text/plain"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]