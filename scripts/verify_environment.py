#!/usr/bin/env python
"""Environment verification script for Component 2."""

import sys
import importlib

REQUIRED_PACKAGES = [
    "osmnx",
    "geopandas",
    "shapely",
    "networkx",
    "pyproj",
    "pandas",
    "numpy",
    "pytest",
]

def main() -> int:
    print("=" * 60)
    print("Component 2 — Environment Verification")
    print("=" * 60)
    print(f"Python Version    : {sys.version.split()[0]}")
    print(f"Python Executable : {sys.executable}")
    print("-" * 60)
    print("Installed Core Packages:")

    all_succeeded = True
    for pkg_name in REQUIRED_PACKAGES:
        try:
            mod = importlib.import_module(pkg_name)
            version = getattr(mod, "__version__", "unknown")
            print(f"  [OK]     {pkg_name:<15}: {version}")
        except ImportError as e:
            all_succeeded = False
            print(f"  [FAILED] {pkg_name:<15}: {e}")

    print("-" * 60)
    if all_succeeded:
        print("Verification Result: SUCCESS — All core dependencies imported cleanly.")
        print("=" * 60)
        return 0
    else:
        print("Verification Result: FAILED — One or more packages could not be imported.")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
