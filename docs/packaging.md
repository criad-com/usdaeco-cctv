# Compatible dependency packaging (0.4.8)

| Dependency | Compatible requirement | Reproducible build source |
|---|---|---|
| Core schema | `>=0.8.1,<0.9` | v0.8.3, `fd87fff09cce26ba132082e5dad721ec2210a7e2` |
| Shared toolchain | pinned source | v0.1.0, `01f6caebf5d6bfb9eeba51a1251fa965f062f919` |

`library.json` and generated `Info.aeco.requires` declare the core range.
`dependencies.json` records that range separately from its exact `ref` and
`revision`. The flake and neutral lock select that exact revision; source
pins provide reproducibility without restricting compatible runtime versions.
The core lock's NAR hash was computed from the immutable Git snapshot.
CCTV schema metadata remains 0.2.1. Current verification against compatible
core v0.8.4 is **123 checks, 0 failed; 297 pytest tests passed**, including
the fresh Revit export claim; three optional performance probes were skipped.
See [release acceptance](acceptance.md) for importer evidence and the Nix limit.

## Historical 0.4.2 packaging acceptance

The current 0.4.8 results and single offline Nix attempt are in
[release acceptance](acceptance.md). The source-closure evidence below records
the previous packaging run and remains historical.

The source set uses core v0.8.1, CCTV v0.4.2, Sync v0.4.1, generator v0.2.1,
scenarios v0.3.0, shared toolchain v0.1.0 and BuildUp/Wall/Pipe v0.1.2.
These are independently mergeable packaging changes; released tags are not
moved and no PR depends on an unmerged sibling branch.

| This repository | Measured result |
|---|---:|
| check.py | 118 checks, 0 failed |
| pytest | 203 passed |
| Source/metadata sanitization | 0 hits |
| Native family/project files committed | 0 |

The portable pytest run passes 200 tests and skips three facility cases.
Supplying the freshly generated/imported facility then passes those three:
203 distinct cases pass in total. No new p95 performance measurement is claimed.

## Nix closure results

Exactly one `nix flake check` was attempted per repository with a flake, using
an offline registry entry plus explicit path overrides. The other repositories
have no flake. Nested registry resolution succeeded; reporting every failure as
an unresolved nested input would be inaccurate.

| Repository | Outcome | Boundary |
|---|---|---|
| `usdaeco-cctv-exec` | PASS | Native check and pytest passed with explicit overrides. |
| `usdaeco-sync` | NOT PROVEN — unavailable build closure | Nested input resolved; source dependencies were not cached; stopped at 180 s. |
| `usdaeco-cctv` | NOT PROVEN — broken importer dependency | Nested input resolved; pinned Nixpkgs marks Python 3.14 IfcOpenShell 0.8.0 broken. |
| `usdaeco-scenarios` | NOT PROVEN — unavailable build closure | Nested input resolved; uncached toolchain/Python dependencies; interrupted without a second attempt. |
| `usdaeco-core` | NO FLAKE | No flake.nix; zero evaluation attempts. |
| `usdaeco-datacentre` | NO FLAKE | No flake.nix; zero evaluation attempts. |

Committed locks retain exact Git revisions and NAR hashes, with deployment-neutral
placeholder URLs. They were generated from local immutable Git snapshots by
metadata-only resolution after the single check attempts; no second flake check
was run. The final neutral lock serialization is **not proven** by a fresh Nix
build. A deployment must supply the overrides below; placeholder URLs are not
fetchable. Deployment addresses and absolute paths never enter committed locks.

## Reproduce the dependency closure

Set `PYTHON` to the Python environment described in the root README. The shared
builder needs USD 26.8, Jinja2 and packaging; full checks also need IfcOpenShell,
NumPy, pytest and the repository's declared Python dependencies. Native exec
instead uses the matching USD development Python 3.14.

Keep the released family repositories as siblings and check out the exact
revisions in the dependency manifest. Set `PROCESSING_TOOLCHAIN` to an owned
checkout at processing-toolchain revision
`d51bae1cf9023cb58e6aed8d39694a181ffe1899`, and `OPENUSD_SOURCE` to an owned
checkout at `47154dc7b5e28df623745495a7a508b69535ba24`.
These are source-root variables, not paths to installations. Registering the
processing toolchain alone is insufficient for nested inputs on some Nix versions.

```sh
nix registry add aeco-toolchain "path:$PROCESSING_TOOLCHAIN"
```

From this repository, with the pinned shared builder at `../usdaeco-toolchain`:

```sh
nix flake check --offline --no-write-lock-file --max-jobs 0 \
  --override-input toolchain "path:../usdaeco-toolchain" \
  --override-input toolchain/aeco-toolchain "path:$PROCESSING_TOOLCHAIN" \
  --override-input toolchain/aeco-toolchain/openusd "path:$OPENUSD_SOURCE" \
  --override-input core "path:../usdaeco-core"
```

`--max-jobs 0` makes an unavailable offline build closure fail without
starting dependency builds. In a build environment authorized to acquire its
dependencies, remove that limit and offline mode. These recipes are **not
enforced** by the Python checks. CCTV deliberately includes the IFC/COBie
importer in its Nix check; it does not silently drop a broken importer dependency.

## Deviations

- No live host was contacted. Recorded native evidence and hardware, lighting,
  retention and between-sample security claims are **not enforced** offline.
- The stable scenarios checkout lacked v0.3.0 at the initial inspection. Sync's
  integration pin therefore records merged main revision
  `fcbc5469c590ba89cf6447ee62c46a5e1d2a5d63`, as directed. The working clone has
  the release tag; no tag was created or moved by this package.
- The shared contract reader is available in CCTV 0.4.8 and Sync 0.4.5.
  Sync retains its standalone fallback for older or absent companions and
  the no-USD Blender route; both paths have dictionary parity tests.
- Historical release commits retain their original compatible schema ranges.
  The scenarios gate verifies those `requires` against the current released
  tags; the separate strict candidate audit passes 29/29 declarations/tag checks.
- This release uses MIT; the root README names the runtime dependencies and
  their licences.
