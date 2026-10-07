"""Typed entry point executed by Core's in-process skill runner."""


def main(x, y):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke("document_sample_pixel", x=x, y=y)


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
