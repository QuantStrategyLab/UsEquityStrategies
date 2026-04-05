__all__ = [
    "STRATEGY_CATALOG",
    "STRATEGY_DEFINITIONS",
    "get_compatible_platforms",
    "get_profile_aliases",
    "get_strategy_catalog",
    "get_strategy_index_rows",
    "get_strategy_definition",
    "get_strategy_definitions",
    "get_strategy_metadata",
    "get_strategy_metadata_map",
    "get_strategy_platform_compatibility_map",
    "resolve_canonical_profile",
]


def __getattr__(name: str):
    if name in {
        "STRATEGY_CATALOG",
        "STRATEGY_DEFINITIONS",
        "get_profile_aliases",
        "get_compatible_platforms",
        "get_strategy_catalog",
        "get_strategy_index_rows",
        "get_strategy_definition",
        "get_strategy_definitions",
        "get_strategy_metadata",
        "get_strategy_metadata_map",
        "get_strategy_platform_compatibility_map",
        "resolve_canonical_profile",
    }:
        from . import catalog as _catalog

        return getattr(_catalog, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
