{
  description = "A very basic flake";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
  };
  outputs = inputs: 
    let 
      system = "x86_64-linux";
      pkgs = inputs.nixpkgs.legacyPackages.${system};
    in
  {
    devShells.${system}.default = pkgs.mkShell {
      nativeBuildInputs = with pkgs; [
        python3
        uv
        gtk4
        gtk4-layer-shell
        gobject-introspection
        ninja
        pkg-config
        gcc
        libpulseaudio
        pulseaudio
      ];
      env.LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
        pkgs.stdenv.cc.cc.lib
        pkgs.gtk4-layer-shell
        pkgs.libpulseaudio
      ];
      shellHook = ''
        export GI_TYPELIB_PATH="${pkgs.gtk4}/lib/girepository-1.0:$GI_TYPELIB_PATH";
        export GI_TYPELIB_PATH="${pkgs.gobject-introspection}/lib/girepository-1.0:$GI_TYPELIB_PATH";
        export GI_TYPELIB_PATH="${pkgs.gtk4-layer-shell}/lib/girepository-1.0:$GI_TYPELIB_PATH";
      '';
    };
  };
}