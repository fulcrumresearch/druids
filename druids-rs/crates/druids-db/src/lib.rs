//! Database layer for Druids using SQLx.
//!
//! This crate provides compile-time verified database queries for Postgres.

pub mod error;
pub mod models;
pub mod pool;
pub mod crypto;

pub use error::{DatabaseError, Result};
pub use pool::{create_pool, Pool};
