"""Login pipeline step (config/sso.py): NetBox's groups follow Authentik's.

At every OIDC login the user is put in exactly the NetBox groups named in the
token's groups claim (made when missing), and is a superuser exactly when one
of them is NETBOX_SUPERUSER_GROUP (authentik Admins by default; NetBox 4 has no
separate staff flag). A token without the claim removes the role rather than
keeping an old one.
"""
from os import environ

from users.models import Group

SUPERUSER_GROUP = environ.get("NETBOX_SUPERUSER_GROUP", "authentik Admins")


def sync_groups(response, user, *args, **kwargs):
    names = response.get("groups")
    if not isinstance(names, list):
        names = []
    user.groups.set([Group.objects.get_or_create(name=name)[0] for name in names])
    admin = SUPERUSER_GROUP in names
    if user.is_superuser != admin:
        user.is_superuser = admin
        user.save(update_fields=["is_superuser"])
