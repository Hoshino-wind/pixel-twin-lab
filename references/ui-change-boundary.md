# UI change boundary

Read this reference only when a target file mixes presentation with product logic, when a global style change has broad reach, or when `pixel-twin begin` requires an explicit `--editable` support file.

## Ownership rule

The task owns presentation, not application behavior. Existing props, callbacks, routes, requests, state, and data are inputs to the UI and must continue to behave the same way after the visual edit.

## Allowed changes

| Area | Examples |
| --- | --- |
| View composition | wrappers, ordering, grouping, semantic elements |
| Layout | grid, flex, positioning, constraints, responsive breakpoints |
| Visual styling | color, typography, spacing, border, radius, shadow, opacity |
| Presentational state | accordion visibility, tabs that already exist, hover/focus/pressed feedback |
| Accessibility | labels, roles, focus order, reduced-motion treatment |
| Product assets | referenced images, icons, SVGs, fonts, textures |

Local UI state is presentational only when it changes visibility or visual feedback without changing stored data, navigation, permissions, requests, or business outcomes.

## Protected behavior

Do not add, remove, or change:

- network requests, endpoints, query keys, mutations, or response mapping;
- stores, reducers, persistence, caches, or shared state contracts;
- route definitions, redirects, guards, middleware, or server actions;
- authentication, authorization, payments, telemetry, or experiments;
- schema, DTO, validation, database, backend, or infrastructure code;
- form submission behavior or business validation;
- handler arguments, callback timing, or user-visible business outcomes.

Do not replace existing data with mock content to match a screenshot. When the reference contains different copy or values, preserve live data unless the user explicitly asks for content changes.

## Mixed UI and logic files

For a TSX, JSX, Vue, or Svelte file that also owns behavior:

1. Identify the existing data reads, effects, callbacks, and handlers before editing.
2. Leave those statements byte-for-byte unchanged when possible.
3. Restrict the patch to markup, presentational props, classes, styles, accessibility attributes, and purely visual state.
4. Reuse existing handler references instead of wrapping them in new business logic.
5. Review the final hunk, not only the final file.

If a visual requirement cannot be met without changing protected behavior, stop and explain the exact dependency. Do not silently broaden the task.

## Shared styles and tokens

Prefer the narrowest existing owner. A shared token is appropriate only when the same semantic value is already shared by every affected consumer. Otherwise keep the change local to the target feature.

Before changing a global stylesheet or theme token:

- search all consumers;
- confirm the new value is correct for them;
- avoid renaming or deleting a public token;
- run the project-native checks once after the complete edit.

## Explicit support files

Plain `.ts`, `.js`, or `.json` files are not assumed to be UI-only. Add an exact path with `pixel-twin begin --editable <path>` only after confirming the file contains presentation data such as theme tokens, icon metadata, animation presets, or static display copy.

`--editable` does not override protected API, store, server, schema, package, lockfile, or infrastructure paths.

## Guard limits

The CLI blocks protected paths and high-confidence behavioral deltas such as new requests, server actions, persistence, and store/service imports. Static checks cannot prove that arbitrary mixed UI code preserves all behavior. The final Git diff review remains the authoritative boundary check.
