{
  description = "NakaVid development shell";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          inherit system;
        };
      in
      {
        devShells.default = pkgs.mkShell {
          NIX_LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
            pkgs.stdenv.cc.cc.lib
            pkgs.zlib
          ];

          packages = with pkgs; [
            python312
            uv
            ruff
            docker
            docker-compose
            ffmpeg
            postgresql_16
          ];

          shellHook = ''
            export UV_PYTHON_DOWNLOADS=never
            export NIX_LD="${pkgs.stdenv.cc.bintools.dynamicLinker}"
            export LD_LIBRARY_PATH="$NIX_LD_LIBRARY_PATH''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
            echo "NakaVid dev shell ready."
            echo "Run: uv sync --all-groups"
          '';
        };
      });
}
