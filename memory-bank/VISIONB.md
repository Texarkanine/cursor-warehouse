# VISIONB: Marketplace Publication & Release Automation

> Ship cursor-warehouse to the public Cursor marketplace and set up automated versioning.

## What This Is

Two small tasks to close the loop on VISIONA: get the plugin listed publicly and automate future releases.

## Scope

### 1. Marketplace Submission

Submit cursor-warehouse to the public Cursor marketplace at [cursor.com/marketplace/publish](https://cursor.com/marketplace/publish).

**Pre-submission checklist** (per Cursor's official [review-plugin-submission](https://cursor.com/marketplace/skills/review-plugin-submission) skill):

- [ ] `.cursor-plugin/plugin.json` exists, valid JSON, `name` is lowercase kebab-case
- [ ] `author` field populated (decide: personal name or org)
- [ ] `description`, `version`, `license` all present
- [ ] Skills in `skills/*/SKILL.md` with `name` and `description` frontmatter
- [ ] Hooks in `hooks/hooks.json`
- [ ] `README.md` covers purpose, installation, and component listing
- [ ] No broken file references or missing paths
- [ ] Optional: logo image (`.cursor-plugin/` or `assets/`)

**What needs fixing before submission:**

- `plugin.json` `homepage` and `repository` URLs point to `cursor-warehouse/cursor-warehouse` — update to `Texarkanine/cursor-warehouse`
- `author` field is missing — add `{ "name": "..." }`
- Consider adding a logo (not required but improves marketplace listing)

**Submission process:**

1. Merge VISIONA PR to main
2. Run the Cursor `review-plugin-submission` quality audit
3. Fix any findings
4. Submit at [cursor.com/marketplace/publish](https://cursor.com/marketplace/publish)
5. Manual review by Cursor team — expect feedback or approval

**Post-approval:**

Once live, updates are automatic: push to main triggers GitHub webhook → marketplace re-indexes.

### 2. Release-Please Setup

Automate version bumps, changelog, and GitHub Releases using [release-please](https://github.com/googleapis/release-please).

Cursor's marketplace doesn't use git tags — it reads `version` from `plugin.json` on the default branch. But release-please still provides:
- Automated `plugin.json` version bumps from conventional commits
- Git tags for audit/rollback
- GitHub Releases with generated changelogs
- Discipline around semver without manual bookkeeping

**Implementation:**

1. Add `.github/workflows/release-please.yml` — GitHub Action triggered on push to main
2. Add `release-please-config.json` targeting `.cursor-plugin/plugin.json` as the version file
3. Add `.release-please-manifest.json` with initial version
4. Verify release-please can bump `version` in the JSON manifest (may need `extra-files` or `json-path` config)

**Conventional commit prefixes** (already in use via niko workflow):
- `feat:` → minor bump
- `fix:` → patch bump
- `feat!:` / `BREAKING CHANGE:` → major bump

### 3. Platform Testing

VISIONA was tested on Windows 11 + WSL only. Native Windows and macOS are expected to work but unverified.

- [ ] Test on macOS (native) — `~/.cursor/` paths, no WSL bifurcation
- [ ] Test on native Windows (no WSL) — PowerShell invocation, Windows `Path.home()`
- [ ] Fix any platform-specific issues discovered
- [ ] Update README platform support section with confirmed platforms

## What This Does NOT Include

- Python packaging (pyproject.toml, uv.lock)
- Supply chain hardening
- Multi-harness support / adapter protocol
- Token enrichment from `state.vscdb`

## Estimated Complexity

**L1** — Two configuration tasks with no code changes. The marketplace submission is a manual form; release-please is a GitHub Action workflow file.

## Acceptance Criteria

1. cursor-warehouse appears on [cursor.com/marketplace](https://cursor.com/marketplace) as a public plugin
2. Installing from the marketplace loads all 4 skills and the session-start hook
3. Pushing a `feat:` commit to main creates a release-please PR that bumps `plugin.json` version
4. Merging the release-please PR creates a GitHub Release with tag and changelog
