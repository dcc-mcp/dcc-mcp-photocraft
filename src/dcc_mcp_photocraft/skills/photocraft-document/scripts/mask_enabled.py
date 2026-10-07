"""Typed entry point executed by Core's in-process skill runner."""


def main(layer, enabled):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke("mask_enabled", layer=layer, enabled=enabled)


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
