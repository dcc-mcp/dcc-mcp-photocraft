"""Typed entry point executed by Core's in-process skill runner."""


def main(
    layer,
    strength=6,
    preserve_details=60,
    reduce_color_noise=45,
    sharpen_details=25,
    remove_jpeg_artifact=False,
):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke(
        "noise_reduce",
        layer=layer,
        strength=strength,
        preserve_details=preserve_details,
        reduce_color_noise=reduce_color_noise,
        sharpen_details=sharpen_details,
        remove_jpeg_artifact=remove_jpeg_artifact,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
