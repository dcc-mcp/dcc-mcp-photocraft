"""Typed entry point executed by Core's in-process skill runner."""


def main(layer):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke("layer_duplicate", layer=layer)


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
