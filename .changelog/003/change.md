# v003: config-system

## What Changed

Refined the configuration system with fixes to config merge logic and full end-to-end verification:

- **Config Merge Fix**:
  - Changed `SavedConfig.base_url` from `Url` to `Option<Url>`
  - Now properly distinguishes "not set" from "set to default value"
  - Users can explicitly configure `https://druids.dev` without it being silently ignored
  - No more fragile string comparisons to detect default URLs

- **Compilation Fixes**:
  - Added `From<dotenvy::Error>` implementation to `ConfigError`
  - Routes IO errors to `IoError` variant, other errors to `EnvFileError`
  - Enables proper `?` error propagation from dotenvy library calls

- **Test Improvements**:
  - Fixed test parallelism issues by cleaning up env vars immediately after use
  - Added `test_config_merge_respects_all_urls` to verify merge behavior
  - All 11 tests pass (5 client + 6 server)

- **Full Verification** (actual test run on Rust 1.94.1):
  ```
  cargo test -- --test-threads=1
  - 5 client tests passing
  - 6 server tests passing
  - 0 failures
  ```

## Why

This iteration fixed two critical issues from the previous config-system implementation:

1. **Config merge fragility**: The original implementation would compare base_url string values to detect if they matched the default, which is error-prone. By making it `Option<Url>`, we cleanly distinguish "user didn't set this" (None) from "user explicitly set this to the default value" (Some(default_url)).

2. **Missing error conversion**: The dotenvy crate returns its own error type, and without a `From` implementation, we couldn't use `?` for error propagation. Now properly handles both IO errors and other dotenvy errors.

## New Goals

Updated Configuration System section in GOALS.md with the new capabilities verified in this iteration:
- Config merge logic respects all explicitly configured URLs
- ConfigError implements From<dotenvy::Error> with proper routing
- All configuration loading and validation tested end-to-end
