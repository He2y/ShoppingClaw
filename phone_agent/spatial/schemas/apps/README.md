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
   `"status": "draft"` to `"status": "confirmed"`.
3. The explorer will then allow `--auto-import-graph` for this app.
