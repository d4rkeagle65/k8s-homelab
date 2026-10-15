"""Login through Authentik's OIDC (its blueprint, kubernetes/prod/services/
authentik/authentik-extras, client netbox), as Authentik documents for NetBox.
Nothing here trusts a header, so NetBox's port can be reachable from the
management network. custom_pipeline.py maps Authentik's groups claim onto
NetBox's groups and roles at every login.
"""
from os import environ

_ENDPOINT = environ.get("OIDC_ENDPOINT", "")  # https://auth.<domain>/application/o/netbox/

REMOTE_AUTH_ENABLED = True
REMOTE_AUTH_BACKEND = "social_core.backends.open_id_connect.OpenIdConnectAuth"
REMOTE_AUTH_AUTO_CREATE_USER = True
SOCIAL_AUTH_OIDC_ENDPOINT = _ENDPOINT
SOCIAL_AUTH_OIDC_KEY = "netbox"
SOCIAL_AUTH_OIDC_SECRET = environ.get("OIDC_CLIENT_SECRET", "")
LOGOUT_REDIRECT_URL = _ENDPOINT + "end-session/"

# Traefik terminates TLS: trust its X-Forwarded-Proto, or the callback address
# NetBox sends Authentik would be http://.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SOCIAL_AUTH_REDIRECT_IS_HTTPS = True

SOCIAL_AUTH_PIPELINE = (
    "social_core.pipeline.social_auth.social_details",
    "social_core.pipeline.social_auth.social_uid",
    "social_core.pipeline.social_auth.social_user",
    "social_core.pipeline.user.get_username",
    "social_core.pipeline.user.create_user",
    "social_core.pipeline.social_auth.associate_user",
    "netbox.authentication.user_default_groups_handler",
    "social_core.pipeline.social_auth.load_extra_data",
    "social_core.pipeline.user.user_details",
    "netbox.custom_pipeline.sync_groups",
)
