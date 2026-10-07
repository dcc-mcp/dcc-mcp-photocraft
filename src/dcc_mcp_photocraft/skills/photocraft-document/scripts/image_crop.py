"""Typed entry point executed by Core's in-process skill runner."""


def main(x, y, width, height, delete_cropped_pixels=True):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke(
        "image_crop",
        x=x,
        y=y,
        width=width,
        height=height,
        delete_cropped_pixels=delete_cropped_pixels,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
