"""Build the matched-control audit package without requiring DGL GraphBolt.

The audit reads ordinary DGL graph snapshots but never uses GraphBolt sampling.
DGL 2.2.1 imports GraphBolt eagerly, so this entry point supplies a no-op module
for that unused optional subsystem. It does not patch the installed package.
"""

from __future__ import annotations

import sys
import types


class _UnusedGraphBoltType:
    def __init__(self, *args, **kwargs) -> None:
        pass


graphbolt_stub = types.ModuleType("dgl.graphbolt")
graphbolt_stub.__file__ = "<unused-graphbolt-stub>"
graphbolt_stub.__getattr__ = lambda name: _UnusedGraphBoltType
sys.modules.setdefault("dgl.graphbolt", graphbolt_stub)

from src.runtime_nfr_cli import (  # noqa: E402
    build_nfr_v3_manual_audit_package_command,
    register_runtime_nfr_subparsers,
)

import argparse  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    register_runtime_nfr_subparsers(subparsers)
    args = parser.parse_args(
        ["build-nfr-v3-manual-audit-package", *sys.argv[1:]]
    )
    build_nfr_v3_manual_audit_package_command(args)


if __name__ == "__main__":
    main()
