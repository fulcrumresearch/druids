# v001: scaffold

## What Changed

Created the initial Rust workspace scaffold for the Druids rewrite:

- **Workspace structure**: Established `druids-rs/` with 6 crates following the architecture outlined in CLAUDE.md
  - `druids-core`: Shared types and utilities
  - `druids-server`: HTTP API server (Axum-based)
  - `druids-bridge`: Agent runtime bridge
  - `druids-client`: CLI tool
  - `druids-db`: Database layer (SQLx + PostgreSQL)
  - `druids-runtime`: Program execution runtime

- **Build infrastructure**:
  - Configured strict clippy linting (`clippy.toml` with `warn-on-all-wildcard-imports = true`)
  - Set up CI/CD workflows for testing and releases
  - Established Rust 1.85 toolchain baseline
  - All workspace checks passing (build, test, clippy, fmt)

- **Code quality fix**: Removed wildcard re-export (`pub use api::*;`) from druids-server to comply with clippy rules

## Why

This scaffold provides the foundation for translating the Python Druids implementation to Rust. The crate structure mirrors the logical separation of concerns (server, bridge, client, runtime) while ensuring strict code quality from day one.

## New Goals

Added build quality gates to GOALS.md:
- `cargo clippy --workspace --all-targets --all-features -- -D warnings` passes with no warnings
- druids-server/src/lib.rs uses `pub mod api` (no wildcard re-exports)

These ensure the codebase maintains high standards as implementation work proceeds.
