"""NetBox's logging: warnings and errors to the container's output, where
Dockhand shows them. The base image ships this file commented out, so an
error page (a 500) left no trace; django.request logs each one with its
traceback. Replaces the image's own logging.py.
"""

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "{levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "loggers": {
        "django": {"handlers": ["console"], "level": "WARNING"},
        "netbox": {"handlers": ["console"], "level": "INFO"},
        "social": {"handlers": ["console"], "level": "INFO"},
    },
}
