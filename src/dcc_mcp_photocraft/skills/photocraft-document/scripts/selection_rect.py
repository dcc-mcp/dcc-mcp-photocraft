"""Typed entry point executed by Core's in-process skill runner."""


def main(x, y, width, height, mode="replace", ellipse=False, feather=0):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke(
        "selection_rect",
        x=x,
        y=y,
        width=width,
        height=height,
        mode=mode,
        ellipse=ellipse,
        feather=feather,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
