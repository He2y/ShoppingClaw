"""Per-app onboarding profile for the AMSG exploration pipeline.

Each unfamiliar app gets an AppProfile that captures:
- Identity (app_id, package, schema, aliases)
- Status (draft → confirmed)
- Coverage overrides (page_types + transitions specific to this app)
- Safety vocabulary (unsafe_tokens, trap_tokens in the app's own language)
- Exploration metadata (default_task, onboarding_notes)

Files live in ``spatial/schemas/apps/<app_id>.yaml``.
Loading uses ``json.loads`` — do NOT use YAML-only syntax.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

APPS_DIR = Path(__file__).resolve().parent / "schemas" / "apps"

_README_CONTENT = """\
# App Profile Directory

Each file `<app_id>.yaml` stores onboarding metadata for one app.

## Format

JSON-compatible YAML (parsed with json.loads).
Do NOT use YAML-only syntax (no unquoted colons, no anchors, no tags).

## Fields

- `app_id`                  ASCII snake_case identifier (matches filename stem)
- `display_name`            Human-readable name
- `package`                 Android package name
- `schema_name`             Domain schema (shopping / common_mobile / ...)
- `aliases`                 List of alternate names / display names
- `status`                  "draft" (blocked from auto-import) or "confirmed" (safe to import)
- `coverage_page_types`     Page types to cover during exploration
- `coverage_transitions`    [[source, target], ...] transitions to cover
- `unsafe_tokens`           Dangerous button labels **in the app's own UI language**
- `trap_tokens`             Labels for trap pages (live streams, ads, games, etc.)
- `login_wall_on_launch`    true if app shows login wall immediately on launch
- `default_task`            Natural-language task for OfflineExplorer
- `onboarding_notes`        Free-text notes from onboarding VLM or human review

## Editing

1. Run `python -m phone_agent.memory.exploration.onboarding --app <name>`
   to auto-generate a draft profile.
2. Review the draft YAML, correct any fields, then change
   `"status": "draft"` → `"status": "confirmed"`.
3. The explorer will then allow `--auto-import-graph` for this app.
"""


@dataclass(frozen=True)
class AppProfile:
    """Identity + exploration configuration for a single app."""

    app_id: str
    display_name: str
    package: str
    schema_name: str
    aliases: tuple[str, ...] = ()
    status: str = "draft"
    coverage_page_types: tuple[str, ...] = ()
    coverage_transitions: tuple[tuple[str, str], ...] = ()
    unsafe_tokens: tuple[str, ...] = ()
    trap_tokens: tuple[str, ...] = ()
    login_wall_on_launch: bool = False
    default_task: str = ""
    onboarding_notes: str = ""

    # Convenience: SafetyPolicy.from_schema reads these names
    @property
    def extra_unsafe_tokens(self) -> tuple[str, ...]:
        return self.unsafe_tokens

    @property
    def extra_trap_tokens(self) -> tuple[str, ...]:
        return self.trap_tokens

    def to_dict(self) -> dict:
        return {
            "_comment": (
                "App onboarding profile. JSON-compatible YAML "
                "(json.loads) — do NOT use YAML-only syntax."
            ),
            "app_id": self.app_id,
            "display_name": self.display_name,
            "package": self.package,
            "schema_name": self.schema_name,
            "aliases": list(self.aliases),
            "status": self.status,
            "coverage_page_types": list(self.coverage_page_types),
            "coverage_transitions": [list(pair) for pair in self.coverage_transitions],
            "unsafe_tokens": list(self.unsafe_tokens),
            "trap_tokens": list(self.trap_tokens),
            "login_wall_on_launch": self.login_wall_on_launch,
            "default_task": self.default_task,
            "onboarding_notes": self.onboarding_notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AppProfile":
        raw_trans = data.get("coverage_transitions") or []
        coverage_transitions = tuple(
            (str(item[0]), str(item[1]))
            for item in raw_trans
            if isinstance(item, (list, tuple)) and len(item) >= 2
        )
        return cls(
            app_id=str(data.get("app_id") or ""),
            display_name=str(data.get("display_name") or ""),
            package=str(data.get("package") or ""),
            schema_name=str(data.get("schema_name") or "common_mobile"),
            aliases=tuple(str(a) for a in (data.get("aliases") or ())),
            status=str(data.get("status") or "draft"),
            coverage_page_types=tuple(
                str(s) for s in (data.get("coverage_page_types") or ())
            ),
            coverage_transitions=coverage_transitions,
            unsafe_tokens=tuple(
                str(s) for s in (data.get("unsafe_tokens") or ())
            ),
            trap_tokens=tuple(
                str(s) for s in (data.get("trap_tokens") or ())
            ),
            login_wall_on_launch=bool(data.get("login_wall_on_launch") or False),
            default_task=str(data.get("default_task") or ""),
            onboarding_notes=str(data.get("onboarding_notes") or ""),
        )


class AppProfileRegistry:
    """Loads and resolves per-app onboarding profiles from YAML files.

    Files are located at ``<profile_dir>/<app_id>.yaml``.
    Missing directories are tolerated (returns None on lookup).
    """

    def __init__(self, profile_dir: str | Path | None = None) -> None:
        self.profile_dir = Path(profile_dir) if profile_dir else APPS_DIR
        self._cache: dict[str, AppProfile] = {}
        self._loaded_all: bool = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_file(self, path: Path) -> AppProfile | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        profile = AppProfile.from_dict(data)
        return profile if profile.app_id else None

    def _ensure_all_loaded(self) -> None:
        if self._loaded_all:
            return
        if not self.profile_dir.exists():
            self._loaded_all = True
            return
        for path in self.profile_dir.glob("*.yaml"):
            stem = path.stem
            if stem not in self._cache:
                profile = self._load_file(path)
                if profile:
                    self._cache[profile.app_id] = profile
        self._loaded_all = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self, app_id: str) -> AppProfile | None:
        """Load profile by exact app_id (file name stem)."""
        if app_id in self._cache:
            return self._cache[app_id]
        path = self.profile_dir / f"{app_id}.yaml"
        if not path.exists():
            return None
        profile = self._load_file(path)
        if profile:
            self._cache[profile.app_id] = profile
        return profile

    def resolve(self, app_name_or_package: str) -> AppProfile | None:
        """Resolve by app_id / display_name / alias / package name.

        Case-insensitive match. Loads all profiles on first call.
        """
        self._ensure_all_loaded()
        key = (app_name_or_package or "").strip().lower()
        if not key:
            return None
        for profile in self._cache.values():
            candidates = (
                profile.app_id.lower(),
                profile.display_name.lower(),
                profile.package.lower(),
                *(a.lower() for a in profile.aliases),
            )
            if key in candidates:
                return profile
        return None

    def save(self, profile: AppProfile) -> Path:
        """Persist a profile to ``<profile_dir>/<app_id>.yaml``.

        Creates the directory (and its README) if needed.
        Returns the written path.
        """
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        readme = self.profile_dir / "README.md"
        if not readme.exists():
            readme.write_text(_README_CONTENT, encoding="utf-8")

        path = self.profile_dir / f"{profile.app_id}.yaml"
        path.write_text(
            json.dumps(profile.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self._cache[profile.app_id] = profile
        self._loaded_all = False  # force re-scan on next list_profiles()
        return path

    def list_profiles(self) -> tuple[AppProfile, ...]:
        """Return all profiles found in the profile directory."""
        self._loaded_all = False
        self._ensure_all_loaded()
        return tuple(sorted(self._cache.values(), key=lambda p: p.app_id))


_DEFAULT_PROFILE_REGISTRY: AppProfileRegistry | None = None


def get_default_profile_registry() -> AppProfileRegistry:
    global _DEFAULT_PROFILE_REGISTRY
    if _DEFAULT_PROFILE_REGISTRY is None:
        _DEFAULT_PROFILE_REGISTRY = AppProfileRegistry()
    return _DEFAULT_PROFILE_REGISTRY
