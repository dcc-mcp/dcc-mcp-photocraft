"""Typed entry point executed by Core's in-process skill runner."""


def main(
    kind,
    brightness=0,
    contrast=0,
    exposure=0,
    offset=0,
    gamma=1,
    in_black=0,
    in_white=255,
    out_black=0,
    out_white=255,
    points=None,
):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke(
        "adjustment_tone",
        kind=kind,
        brightness=brightness,
        contrast=contrast,
        exposure=exposure,
        offset=offset,
        gamma=gamma,
        in_black=in_black,
        in_white=in_white,
        out_black=out_black,
        out_white=out_white,
        points=points,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
