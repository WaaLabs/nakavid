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
          packages = with pkgs; [
            python312
            uv
            docker
            docker-compose
            ffmpeg
            postgresql_16
          ];

          shellHook = ''
            export UV_PYTHON_DOWNLOADS=never
            echo "NakaVid dev shell ready."
            echo "Run: uv sync --all-groups"
          '';
        };
      });
}
