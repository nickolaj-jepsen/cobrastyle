use std::fs;
use std::path::Path;

/// Extracts the exact lightningcss version from Cargo.lock so the Python
/// module can report it (class-name hashes are not stable across versions).
fn lightningcss_version() -> Option<String> {
    let manifest_dir = std::env::var("CARGO_MANIFEST_DIR").ok()?;
    let lock = fs::read_to_string(Path::new(&manifest_dir).join("Cargo.lock")).ok()?;
    let mut in_package = false;
    for line in lock.lines() {
        let line = line.trim();
        if line == "[[package]]" {
            in_package = false;
        } else if line == "name = \"lightningcss\"" {
            in_package = true;
        } else if in_package && line.starts_with("version = \"") {
            return Some(
                line.trim_start_matches("version = \"")
                    .trim_end_matches('"')
                    .to_string(),
            );
        }
    }
    None
}

fn main() {
    println!("cargo:rerun-if-changed=Cargo.lock");
    let version = lightningcss_version().unwrap_or_else(|| "unknown".to_string());
    println!("cargo:rustc-env=LIGHTNINGCSS_VERSION={version}");
}
