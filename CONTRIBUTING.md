# Contributing

CAID targets KiCad 10 on Windows. Keep code, comments, commit messages, and technical documentation in English. Interface text belongs in the English/German message catalog.

Before opening a pull request, run:

```sh
python3 -m unittest discover -s tests -q
python3 scripts/build_pcm.py
```

Changes to editor integration also need a manual check in KiCad 10. Include the KiCad version, the project state used for the check, and any known limitation in the pull request. Keep generated archives and local reports under `build/`; do not commit credentials or private project files.
