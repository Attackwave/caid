# Contributing

CAID targets KiCad 10 on Windows. Keep code, comments, commit messages, and technical documentation in English. Interface text belongs in the English/German message catalog.

Before opening a pull request, run:

```sh
python3 -m unittest discover -s tests -q
python3 scripts/build_pcm.py
```

Changes to editor integration also need a manual check in KiCad 10. Include the KiCad version, the project state used for the check, and any known limitation in the pull request. Keep generated archives and local reports under `build/`; do not commit credentials or private project files.

## Releases

Use `vX.Y.Z` for both the Git tag and the GitHub release title. For example, version `0.23.2` has tag and title `v0.23.2`. Attach the versioned PCM and launcher ZIP files and their SHA-256 checksums. Confirm that the installation instructions link to the new release before publishing it.
