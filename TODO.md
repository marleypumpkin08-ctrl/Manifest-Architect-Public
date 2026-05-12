# TODO (v1.2.0)

- [x] Fix update engine infinite loop risk: change version check to strictly `latest > current` using normalized tuple compare.
- [x] Ensure fetch_version_requests uses the same strict comparison and returns `None` when versions match.
- [x] Reduce any unintended auto-update behavior: keep the standalone updater entrypoint check-once.
- [x] Subnautica 2 prep: ensure `_apply_subnautica_2_fix()` in manifest_studio.py delegates to `logic.py` implementation.
- [ ] Confirm version constants: CURRENT_VERSION in code + metadata/version.json are exactly `1.2.0`.
- [ ] Prepare final v1.2.0 release set (ensure only the updated files are included for v1.2.0 artifacts).

