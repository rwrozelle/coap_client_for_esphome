#!/usr/bin/env python3
"""Local HACS validation for integration repositories."""

import json
import sys
from pathlib import Path

import voluptuous as vol
from awesomeversion import AwesomeVersion

HACS_MANIFEST_SCHEMA = vol.Schema(
    {
        vol.Optional("content_in_root"): bool,
        vol.Optional("country"): str,
        vol.Optional("filename"): str,
        vol.Optional("hacs"): str,
        vol.Optional("hide_default_branch"): bool,
        vol.Optional("homeassistant"): str,
        vol.Optional("persistent_directory"): str,
        vol.Optional("render_readme"): bool,
        vol.Optional("zip_release"): bool,
        vol.Required("name"): str,
    },
    extra=vol.PREVENT_EXTRA,
)

INTEGRATION_MANIFEST_SCHEMA = vol.Schema(
    {
        vol.Required("codeowners"): list,
        vol.Required("documentation"): str,
        vol.Required("domain"): str,
        vol.Required("issue_tracker"): str,
        vol.Required("name"): str,
        vol.Required("version"): vol.Coerce(AwesomeVersion),
    },
    extra=vol.ALLOW_EXTRA,
)


def validate(repo_root: Path) -> list[str]:
    errors = []

    # Check README
    if not (repo_root / "README.md").exists() and not (repo_root / "readme.md").exists():
        errors.append("Missing README.md")

    # Validate hacs.json
    hacs_json = repo_root / "hacs.json"
    if not hacs_json.exists():
        errors.append("Missing hacs.json")
    else:
        try:
            data = json.loads(hacs_json.read_text())
            HACS_MANIFEST_SCHEMA(data)
        except (json.JSONDecodeError, vol.Invalid) as e:
            errors.append(f"hacs.json invalid: {e}")

    # Find and validate integration manifest
    manifests = list(repo_root.glob("custom_components/*/manifest.json"))
    if not manifests:
        errors.append("No custom_components/*/manifest.json found")
    else:
        for manifest_path in manifests:
            try:
                data = json.loads(manifest_path.read_text())
                INTEGRATION_MANIFEST_SCHEMA(data)
            except (json.JSONDecodeError, vol.Invalid) as e:
                errors.append(f"{manifest_path.relative_to(repo_root)}: {e}")

    return errors


def main() -> int:
    import subprocess
    repo_root = Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"]).decode().strip()
    )

    print("Running HACS local validation...")
    errors = validate(repo_root)

    if errors:
        print(f"\nHACS validation failed ({len(errors)} error(s)):")
        for err in errors:
            print(f"  [ERROR] {err}")
        return 1

    print("HACS validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
