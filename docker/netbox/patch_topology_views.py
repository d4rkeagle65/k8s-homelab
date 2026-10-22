"""Patch netbox-topology-views' RoleImageSerializer so NetBox's change log can render it.

NetBox (4.7) renders a change's object through the model's API serializer with
nested=True (netbox/api/gfk_fields.py, GFKSerializerField), and its own serializers accept
nested, fields and omit (BaseModelSerializer). The plugin's RoleImageSerializer is a plain
DRF ModelSerializer, so every change to a role icon makes /api/core/object-changes/ answer
500 ("unexpected keyword argument 'nested'"). This accepts the three and ignores them.

Run at build time by the Dockerfile with the venv's Python; given a path, patches that
file instead (to test against an unpacked wheel). Fails, so the build fails, when the
class isn't exactly as expected: a plugin release that changed it needs this re-checked,
and one that fixed it needs this file and its Dockerfile lines removed.
"""
import importlib.util
import pathlib
import py_compile
import sys

OLD = "class RoleImageSerializer(ModelSerializer):\n    class Meta:\n"
NEW = (
    "class RoleImageSerializer(ModelSerializer):\n"
    "    # Patched by the homelab NetBox image (docker/netbox/patch_topology_views.py):\n"
    "    # NetBox renders a change's object with nested=True.\n"
    "    def __init__(self, *args, nested=False, fields=None, omit=None, **kwargs):\n"
    "        super().__init__(*args, **kwargs)\n"
    "\n"
    "    class Meta:\n"
)


def main():
    if len(sys.argv) > 1:
        target = pathlib.Path(sys.argv[1])
    else:
        # find_spec locates the package without importing it (its import needs NetBox's settings).
        spec = importlib.util.find_spec("netbox_topology_views")
        if spec is None or not spec.submodule_search_locations:
            sys.exit("netbox_topology_views isn't installed")
        target = pathlib.Path(list(spec.submodule_search_locations)[0]) / "api" / "serializers.py"

    text = target.read_text(encoding="utf-8")
    if text.count(OLD) != 1:
        sys.exit(f"{target}: RoleImageSerializer isn't as expected (found {text.count(OLD)} times); "
                 "re-check docker/netbox/patch_topology_views.py against this plugin version")
    target.write_text(text.replace(OLD, NEW), encoding="utf-8", newline="\n")
    py_compile.compile(str(target), doraise=True)
    print(f"patched {target}")


if __name__ == "__main__":
    main()
