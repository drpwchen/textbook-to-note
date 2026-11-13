# Delta — conversion

## ADDED Requirements

### Requirement: `--resplit --force` keeps figure folders and links

When `--resplit` replaces an existing `<out>/<book>` folder, every folder in it, at any depth,
whose name matches the folders resplit never reads (`figures*`, `images`, `_epub_src`,
`__pycache__`, `.drafts_bak`, `*.bak`) SHALL be moved to the same relative path in the new
folder, unless that path already exists there. A junction or symlink SHALL be moved as a link,
without opening its target. The run report entry SHALL list the moved paths as `kept_dirs`.

#### Scenario: Junction and real folder survive a forced resplit
- **GIVEN** `<out>/BookA/figures` is a junction (symlink off Windows) to a folder outside
  `<out>`, `<out>/BookA/images` is a real folder with one file, and `<out>/BookA/notes_by_hand/`
  exists
- **WHEN** `--resplit --force` runs for BookA
- **THEN** `figures` is still a link to the same target, the target's files are unchanged,
  `images/` still holds its file, `notes_by_hand/` is gone, no `.tmp-*` folder remains, the
  report has `kept_dirs: ["figures", "images"]`, and BookA validates

**Negative constraints**
- MUST NOT follow a link while moving it or while looking for folders to keep.
- MUST NOT keep a folder whose name resplit can write.
