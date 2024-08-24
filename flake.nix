{
  description = "A development environment for cobrastyle";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs";
    flake-utils.url = "github:numtide/flake-utils";
    rust-overlay = {
      url = "github:oxalica/rust-overlay";
      inputs = {
        nixpkgs.follows = "nixpkgs";
      };
    };
  };

  outputs = { self, nixpkgs, flake-utils, rust-overlay }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        overlays = [ (import rust-overlay) ];
        pkgs = import nixpkgs {
          inherit system overlays;
        };
        #rust = pkgs.rust-bin.fromRustupToolchainFile ./rust-toolchain.toml;
        rust = pkgs.rust-bin.stable.latest.default;
      in
      {
        devShell = pkgs.mkShell {
          buildInputs = [
            rust
            pkgs.python312
            pkgs.uv
            pkgs.git
          ];
          RUST_SRC_PATH = "${pkgs.rust.packages.stable.rustPlatform.rustLibSrc}";
          shellHook = ''
            # Sync uv venv
            uv venv -p ${pkgs.python312}/bin/python3 -q
          '';
        };
      });
}
