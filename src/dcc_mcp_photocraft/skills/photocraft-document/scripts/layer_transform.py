"""Typed entry point executed by Core's in-process skill runner."""


def main(
    layer,
    scale_x=1,
    scale_y=None,
    translate_x=0,
    translate_y=0,
    rotation_degrees=0,
    interpolation="bicubic",
):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke(
        "layer_transform",
        layer=layer,
        scale_x=scale_x,
        scale_y=scale_y,
        translate_x=translate_x,
        translate_y=translate_y,
        rotation_degrees=rotation_degrees,
        interpolation=interpolation,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
