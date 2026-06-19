from __future__ import annotations

import argparse
from collections.abc import Sequence
import json
from pathlib import Path
import sys

from .signal_bundle_contract import (
    CANONICAL_INPUT_DERIVED_INDICATORS,
    SignalBundleContractError,
    research_export_audit_summary_from_manifest,
    signal_research_handoff_audit_summary_from_manifest,
    signal_platform_handoff_audit_summary_from_index,
    signal_platform_handoff_audit_summary_from_manifest,
    signal_consumer_contract_registry_audit_summary_from_file,
    signal_consumer_contract_registry_audit_summary_from_manifest,
    signal_bundle_consumer_audit_summary_from_index,
    signal_bundle_consumer_audit_summary_from_manifest,
    signal_bundle_audit_summary_from_index,
    signal_bundle_audit_summary_from_manifest,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.platform_handoff_manifest is not None:
            if (
                args.platform_handoff_index is not None
                or args.research_handoff_manifest is not None
                or args.research_export_manifest is not None
                or args.consumer_contract_registry_manifest is not None
                or args.consumer_contract_registry is not None
                or args.index is not None
                or args.manifest is not None
            ):
                raise SignalBundleContractError(
                    "provide --platform-handoff-manifest without "
                    "--platform-handoff-index, --research-handoff-manifest, "
                    "--research-export-manifest, --consumer-contract-registry-manifest, "
                    "--consumer-contract-registry, manifest, or --index"
                )
            summary = signal_platform_handoff_audit_summary_from_manifest(
                args.platform_handoff_manifest,
                consumer=args.consumer,
                require_all_known_families=args.require_all_known_families,
                require_all_known_consumers=args.require_all_known_consumers,
                require_runtime_consumer_coverage=(
                    args.require_runtime_consumer_coverage
                ),
            )
        elif args.platform_handoff_index is not None:
            if (
                args.research_handoff_manifest is not None
                or args.research_export_manifest is not None
                or args.consumer_contract_registry_manifest is not None
                or args.consumer_contract_registry is not None
                or args.index is not None
                or args.manifest is not None
            ):
                raise SignalBundleContractError(
                    "provide --platform-handoff-index without "
                    "--research-handoff-manifest, --research-export-manifest, "
                    "--consumer-contract-registry-manifest, "
                    "--consumer-contract-registry, manifest, or --index"
                )
            summary = signal_platform_handoff_audit_summary_from_index(
                args.platform_handoff_index,
                consumer=args.consumer,
                expected_canonical_input=args.canonical_input,
                as_of=args.as_of,
                require_all_known_families=args.require_all_known_families,
                require_all_known_consumers=args.require_all_known_consumers,
                require_runtime_consumer_coverage=(
                    args.require_runtime_consumer_coverage
                ),
            )
        elif args.research_handoff_manifest is not None:
            if (
                args.research_export_manifest is not None
                or args.consumer_contract_registry_manifest is not None
                or args.consumer_contract_registry is not None
                or args.index is not None
                or args.manifest is not None
                or args.research_transform is not None
            ):
                raise SignalBundleContractError(
                    "provide --research-handoff-manifest without "
                    "--research-export-manifest, "
                    "--consumer-contract-registry-manifest, "
                    "--consumer-contract-registry, manifest, --index, "
                    "or --research-transform"
                )
            summary = signal_research_handoff_audit_summary_from_manifest(
                args.research_handoff_manifest,
                consumer=args.consumer,
                expected_research_artifact_type=args.research_artifact_type,
                require_all_known_families=args.require_all_known_families,
                require_all_known_consumers=args.require_all_known_consumers,
                require_runtime_consumer_coverage=(
                    args.require_runtime_consumer_coverage
                ),
            )
        elif args.research_export_manifest is not None:
            if (
                args.consumer_contract_registry_manifest is not None
                or args.consumer_contract_registry is not None
                or args.index is not None
                or args.manifest is not None
                or args.consumer
                or args.require_all_known_families
                or args.require_all_known_consumers
                or args.require_runtime_consumer_coverage
            ):
                raise SignalBundleContractError(
                    "provide --research-export-manifest without "
                    "--consumer-contract-registry-manifest, "
                    "--consumer-contract-registry, manifest, --index, "
                    "--consumer, --require-all-known-families, or "
                    "--require-all-known-consumers"
                )
            summary = research_export_audit_summary_from_manifest(
                args.research_export_manifest,
                expected_artifact_type=args.research_artifact_type,
                expected_transform=args.research_transform,
            )
        elif args.consumer_contract_registry_manifest is not None:
            if (
                args.consumer_contract_registry is not None
                or args.index is not None
                or args.manifest is not None
                or args.platform_handoff_index is not None
                or args.research_handoff_manifest is not None
                or args.research_export_manifest is not None
                or args.consumer
                or args.require_all_known_families
                or args.require_runtime_consumer_coverage
            ):
                raise SignalBundleContractError(
                    "provide --consumer-contract-registry-manifest without "
                    "--consumer-contract-registry, manifest, --index, "
                    "--research-handoff-manifest, --research-export-manifest, "
                    "--consumer, or --require-all-known-families"
                )
            summary = signal_consumer_contract_registry_audit_summary_from_manifest(
                args.consumer_contract_registry_manifest,
                expected_canonical_input=args.canonical_input,
                require_all_known_consumers=args.require_all_known_consumers,
            )
        elif args.consumer_contract_registry is not None:
            if (
                args.index is not None
                or args.manifest is not None
                or args.platform_handoff_index is not None
                or args.research_handoff_manifest is not None
                or args.research_export_manifest is not None
                or args.consumer
                or args.require_all_known_families
                or args.require_runtime_consumer_coverage
            ):
                raise SignalBundleContractError(
                    "provide --consumer-contract-registry without manifest, "
                    "--index, --research-handoff-manifest, "
                    "--research-export-manifest, --consumer, or "
                    "--require-all-known-families"
                )
            summary = signal_consumer_contract_registry_audit_summary_from_file(
                args.consumer_contract_registry,
                expected_canonical_input=args.canonical_input,
                require_all_known_consumers=args.require_all_known_consumers,
            )
        elif args.index is not None and args.manifest is not None:
            raise SignalBundleContractError("provide either manifest or --index, not both")
        elif args.research_artifact_type or args.research_transform:
            raise SignalBundleContractError(
                "--research-artifact-type is only valid with "
                "--research-export-manifest or --research-handoff-manifest; "
                "--research-transform is only valid with --research-export-manifest"
            )
        elif args.require_all_known_consumers:
            raise SignalBundleContractError(
                "--require-all-known-consumers is only valid with "
                "--consumer-contract-registry, --consumer-contract-registry-manifest, "
                "--platform-handoff-manifest, --platform-handoff-index, "
                "or --research-handoff-manifest"
            )
        elif args.require_all_known_families:
            raise SignalBundleContractError(
                "--require-all-known-families is only valid with "
                "--platform-handoff-manifest, --platform-handoff-index, "
                "or --research-handoff-manifest"
            )
        elif args.require_runtime_consumer_coverage:
            raise SignalBundleContractError(
                "--require-runtime-consumer-coverage is only valid with "
                "--platform-handoff-manifest, --platform-handoff-index, "
                "or --research-handoff-manifest"
            )
        elif args.index is not None:
            if args.consumer:
                summary = signal_bundle_consumer_audit_summary_from_index(
                    args.index,
                    consumer=args.consumer,
                    expected_canonical_input=args.canonical_input,
                    as_of=args.as_of,
                    bundle_id=args.bundle_id,
                )
            else:
                summary = signal_bundle_audit_summary_from_index(
                    args.index,
                    expected_canonical_input=args.canonical_input,
                    as_of=args.as_of,
                    bundle_id=args.bundle_id,
                )
        elif args.manifest is not None:
            if args.consumer:
                summary = signal_bundle_consumer_audit_summary_from_manifest(
                    args.manifest,
                    consumer=args.consumer,
                )
            else:
                summary = signal_bundle_audit_summary_from_manifest(args.manifest)
        else:
            raise SignalBundleContractError(
                "provide a manifest path, --index, --consumer-contract-registry, "
                "--platform-handoff-manifest, --platform-handoff-index, "
                "--research-export-manifest, or --research-handoff-manifest"
            )
    except (OSError, SignalBundleContractError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(summary, indent=2 if args.pretty else None, sort_keys=True))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a local signal bundle manifest and print non-sensitive "
            "consumer audit metadata."
        )
    )
    parser.add_argument("manifest", nargs="?", type=Path)
    parser.add_argument("--index", type=Path, help="Resolve a manifest from a local bundle index.")
    parser.add_argument(
        "--consumer-contract-registry",
        type=Path,
        help="Validate an external consumer contract registry JSON artifact.",
    )
    parser.add_argument(
        "--consumer-contract-registry-manifest",
        type=Path,
        help=(
            "Validate an external consumer contract registry manifest and its "
            "linked registry JSON artifact."
        ),
    )
    parser.add_argument(
        "--platform-handoff-manifest",
        type=Path,
        help=(
            "Validate a MarketSignalSources platform handoff manifest and its "
            "linked bundle, source-family catalog, and consumer registry manifests."
        ),
    )
    parser.add_argument(
        "--platform-handoff-index",
        type=Path,
        help=(
            "Resolve and validate the latest matching MarketSignalSources platform "
            "handoff manifest from a handoff index."
        ),
    )
    parser.add_argument(
        "--research-export-manifest",
        type=Path,
        help="Validate a MarketSignalSources research_export.v1 CSV manifest.",
    )
    parser.add_argument(
        "--research-handoff-manifest",
        type=Path,
        help=(
            "Validate a MarketSignalSources research handoff manifest and its "
            "linked research export, source-family catalog, and consumer registry manifests."
        ),
    )
    parser.add_argument(
        "--research-artifact-type",
        help="Require a specific research export artifact_type.",
    )
    parser.add_argument(
        "--research-transform",
        help="Require a specific research export transform when validating an export manifest.",
    )
    parser.add_argument("--as-of", help="Select the latest index entry at or before this as_of date.")
    parser.add_argument("--bundle-id", help="Require a specific bundle_id from the index.")
    parser.add_argument("--canonical-input", default=CANONICAL_INPUT_DERIVED_INDICATORS)
    parser.add_argument(
        "--require-all-known-consumers",
        action="store_true",
        help="Require a consumer contract registry to cover every known local consumer.",
    )
    parser.add_argument(
        "--require-all-known-families",
        action="store_true",
        help=(
            "Require a platform handoff source-family catalog manifest to declare "
            "all known families present."
        ),
    )
    parser.add_argument(
        "--require-runtime-consumer-coverage",
        action="store_true",
        help=(
            "Require a handoff source-family catalog manifest to map every known "
            "runtime consumer to an implemented source family."
        ),
    )
    parser.add_argument(
        "--consumer",
        help="Validate required indicator fields for a known strategy or research consumer.",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
