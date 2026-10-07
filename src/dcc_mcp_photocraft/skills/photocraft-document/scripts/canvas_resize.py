"""Typed entry point executed by Core's in-process skill runner."""


def main(width, height, anchor="center", extension_color="transparent"):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke(
        "canvas_resize", width=width, height=height, anchor=anchor, extension_color=extension_color
    )


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
