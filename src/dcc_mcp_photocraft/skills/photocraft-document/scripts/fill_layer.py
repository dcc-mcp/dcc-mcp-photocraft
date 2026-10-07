"""Typed entry point executed by Core's in-process skill runner."""


def main(color, name="Fill", end_color=None, angle=0, style="linear"):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke(
        "fill_layer", color=color, name=name, end_color=end_color, angle=angle, style=style
    )


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
