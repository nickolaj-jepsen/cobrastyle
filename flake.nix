{
  description = "A development environment for cobrastyle";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, flake-utils, rust-overlay }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
          overlays = [ (import rust-overlay) ];
        };
        rust = pkgs.rust-bin.fromRustupToolchainFile ./rust-toolchain.toml;
      in
      {
        devShells.default = pkgs.mkShell {
          packages = [
            rust
            pkgs.python312
            pkgs.uv
            pkgs.just
            pkgs.prek
            pkgs.watchexec
          ];
          RUST_SRC_PATH = "${rust}/lib/rustlib/src/rust/library";
          # Local rebuilds of the extension use the dev profile (fast compile);
          # CI and published wheels build release.
          MATURIN_PEP517_ARGS = "--profile dev";
          shellHook = ''
            uv sync -q --all-packages
            prek install > /dev/null
          '';
        };
      });
}
