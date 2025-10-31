#!/usr/bin/env python3
"""Check script_registry proxy / instance attributes for debugging AttributeError seen in Docker.

Usage: python tools/check_script_registry_attrs.py

This will import the module and print whether list_scripts exists on proxy and on real instance.
"""
import inspect
from econ_sim.script_engine import script_registry, get_script_registry

print("script_registry (proxy):", type(script_registry))
print("has attr 'list_scripts' on proxy?", hasattr(script_registry, "list_scripts"))
print(
    "dir(script_registry) sample:",
    [a for a in dir(script_registry) if a.startswith("list") or a.endswith("scripts")][
        :30
    ],
)

real = get_script_registry()
print("real registry type:", type(real))
print("has attr 'list_scripts' on real?", hasattr(real, "list_scripts"))
print("callable list_scripts?", callable(getattr(real, "list_scripts", None)))

# show whether list_scripts is attribute of class
cls = type(real)
print("list_scripts in class dict?", "list_scripts" in cls.__dict__)
print("inspect.getsource on class (first 200 chars):")
try:
    src = inspect.getsource(cls)
    print(src[:200])
except Exception as e:
    print("failed to getsource:", e)
