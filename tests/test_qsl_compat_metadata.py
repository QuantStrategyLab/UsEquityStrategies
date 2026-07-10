from pathlib import Path
import tomllib


def test_qsl_compat_metadata_exists_and_bundle() -> None:
    root = Path(__file__).resolve().parents[1]
    qsl_path = root / "qsl.toml"
    assert qsl_path.exists(), "qsl.toml missing"
    with qsl_path.open("rb") as f:
        data = tomllib.load(f)
    with (root / "pyproject.toml").open("rb") as f:
        project = tomllib.load(f)

    assert data.get("compat", {}).get("bundle") == "2026.07.3", "compat.bundle mismatch"
    dependency = next(
        value for value in project["project"]["dependencies"] if value.startswith("quant-platform-kit @ ")
    )
    qpk_pin = dependency.rsplit("@", maxsplit=1)[1]
    assert dependency in data["compat"]["requires"]
    assert f"QuantPlatformKit.git?rev={qpk_pin}#{qpk_pin}" in (root / "uv.lock").read_text(encoding="utf-8")
