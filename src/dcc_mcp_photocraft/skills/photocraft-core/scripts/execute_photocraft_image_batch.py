"""Fixed entrypoint for one bounded native image workflow."""

from dcc_mcp_core.skill import skill_entry

from dcc_mcp_photocraft.capability_dispatch import dispatch


@skill_entry
def main(**params):
    return dispatch("execute_photocraft_image_batch", params)


if __name__ == "__main__":
    from dcc_mcp_core.skill import run_main

    run_main(main)
