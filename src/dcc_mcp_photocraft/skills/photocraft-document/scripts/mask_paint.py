"""Typed entry point executed by Core's in-process skill runner."""


def main(layer, points, size=32, hardness=1, opacity=1, value="reveal"):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke(
        "mask_paint",
        layer=layer,
        points=points,
        size=size,
        hardness=hardness,
        opacity=opacity,
        value=value,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
