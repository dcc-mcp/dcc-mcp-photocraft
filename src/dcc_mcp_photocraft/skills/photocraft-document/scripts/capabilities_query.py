"""Typed entry point executed by Core's in-process skill runner."""


def main(query="", category="", status="", offset=0, limit=25):
    from dcc_mcp_photocraft.facade import ACTIVE_FACADE

    return ACTIVE_FACADE.get().invoke(
        "capabilities_query",
        query=query,
        category=category,
        status=status,
        offset=offset,
        limit=limit,
    )


if __name__ == "__main__":
    from dcc_mcp_core.skills_helper import run_main

    run_main(main)
