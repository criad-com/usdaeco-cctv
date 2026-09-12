{
  description = "usdAecoCctv semantic library and example";
  inputs = {
    toolchain.url = "github:criad-com/usdaeco-toolchain?ref=v0.3.10";
    nixpkgs.follows = "toolchain/nixpkgs";
    core.url = "github:criad-com/usdaeco-core?ref=v0.9.5";
    core.inputs.toolchain.follows = "toolchain";
    core.inputs.nixpkgs.follows = "nixpkgs";
    datacentre.url = "github:criad-com/usdaeco-datacentre?ref=v0.4.8";
    datacentre.flake = false;
    ifc.url = "github:criad-com/usdaeco-ifc?ref=v0.2.2";
    ifc.flake = false;
  };
  outputs = { self, nixpkgs, toolchain, core, datacentre, ifc }:
    let
      eachSystem = nixpkgs.lib.genAttrs [ "aarch64-darwin" "x86_64-linux" ];
      forSystem = system:
        let
          kit = toolchain.lib.forSystem system;
          pkgs = nixpkgs.legacyPackages.${system};
          checkPython = pkgs.python3.withPackages (ps: [ kit.usdPython kit.kit ps.numpy ps.jinja2 ps.packaging ps.pytest ps.ifcopenshell ps.openpyxl ]);
          corePlugin = core.packages.${system}.default;
          schema = kit.buildCodelessSchema { name = "usdAecoCctv"; src = self; deps = [ corePlugin ]; };
          plugins = kit.pluginSet { plugins = [ schema ]; };
          setup = ''
            export TOOLCHAIN_DIR=${toolchain}
            export AECO_CORE_ROOT=${core}
            export AECO_IFC_ROOT=${ifc}
            export CORE_PLUGIN_DIR=${corePlugin}/plugins/usdAeco/resources
            export AECO_DATACENTRE_ROOT=${datacentre}
            export PXR_PLUGINPATH_NAME=${plugins}
          '';
          example = pkgs.writeShellApplication {
            name = "example";
            runtimeInputs = [ kit.pythonEnv kit.usd-dev ];
            text = setup + ''
              cp -R ${self} example-work
              chmod -R u+w example-work
              env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT" python example-work/examples/datacentre/run.py "$@"
            '';
          };
          render = pkgs.writeShellApplication {
            name = "render";
            runtimeInputs = [ kit.pythonEnv kit.usd-dev ];
            text = setup + ''
              cp -R ${self} render-work
              chmod -R u+w render-work
              env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT" python render-work/examples/datacentre/run.py "$@"
            '';
          };
        in { inherit kit pkgs checkPython schema plugins setup example render; };
    in {
      packages = eachSystem (system: let p = forSystem system; in {
        default = p.schema;
        pluginSet = p.plugins;
      });
      checks = eachSystem (system: let p = forSystem system; in {
        library = p.pkgs.runCommand "usdAecoCctv-check" {
          nativeBuildInputs = [ p.checkPython p.kit.usd-dev ];
        } (p.setup + ''
          cp -R ${self} source
          chmod -R u+w source
          cd source
          env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT" python check.py
          env -u PYTHONPATH PYTHONPATH="$AECO_CORE_ROOT" python -m pytest -q
          mkdir -p "$out"
        '');
        structure = p.pkgs.runCommand "usdAecoCctv-structure" {
          nativeBuildInputs = [ p.kit.pythonEnv ];
        } (p.setup + ''
          env -u PYTHONPATH python ${self}/tools/check_structure.py
          mkdir -p "$out"
        '');
      });
      devShells = eachSystem (system: let p = forSystem system; in {
        default = p.pkgs.mkShell {
          packages = [ p.kit.pythonEnv p.kit.usd-dev ];
          shellHook = p.setup + "unset PYTHONPATH";
        };
      });
      apps = eachSystem (system: let p = forSystem system; in {
        example = { type = "app"; program = "${p.example}/bin/example"; };
        render = { type = "app"; program = "${p.render}/bin/render"; };
      });
    };
}
