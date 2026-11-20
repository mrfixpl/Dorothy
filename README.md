# Dorothy

```text
  _____                  _   _
 |  __ \                | | | |
 | |  | | ___  _ __ ___ | |_| |__  _   _
 | |  | |/ _ \| '__/ _ \| __| '_ \| | | |
 | |__| | (_) | | | (_) | |_| | | | |_| |
 |_____/ \___/|_|  \___/ \__|_| |_|\__, |
                                  __/ |
    You're Not in .kzb Anymore... |___/
```

Unofficial toolbox for Kanzi Studio files from VAG MIB2 and Virtual Cockpit.

## Quick start

1. Download or clone the **whole repository**. Keep `dorothy.py`, `dorothy_lib/` and `config/` together.
2. Install Python **3.10 or newer**. The application has no pip dependencies.
3. Put original `.kzb` files directly in `input/`.
4. Run `python3 dorothy.py` and choose a menu shortcut, then press ENTER.

The workspace is located next to `dorothy.py`, regardless of your current terminal directory. Scans are shallow. Uppercase `.KZB` extensions are also accepted. A task processes all matching files and returns to the menu. Failures are reported per file and do not stop the remaining batch. `Q` exits, `R` rescans, and Ctrl+C interrupts a task and returns to the menu. Ctrl+D/EOF exits.

**The program never writes to `input/`.** Generated files go to `output/`. Language preferences are stored separately in `config/settings.json`. Existing outputs are preserved; subsequent runs use numbered names. Extraction interrupted by an error may leave partial results in its output folder. Completed rebuilt KZBs are checked and published only after construction finishes.

## Menu

| Shortcut | Task | Output |
|---|---|---|
| `1` | Check KZB integrity (dry run) | Terminal only, no filesystem writes |
| `2` | Extract only `Resource Files` | `output/resources/<name>/Resource Files/` and manifest |
| `3` | Extract all indexed assets, including engine objects | `output/assets/<name>/` and manifest |
| `4` | Losslessly optimise PNG/JPEG and rebuild | `output/optimised/<name>_optimised.kzb` |
| `5` | Rebuild using supplied custom assets | `output/custom/<name>_custom.kzb` |
| `A` | Extract VC `.atlas` assets with companion JSON | `output/atlases/<name>/` |
| `P` | Copy originals from MIB/QNX with SCP | `output/pulled/<host>/` |
| `R` | Refresh the input scan | — |
| `L` | Select a language from `config/lang/` | Saves language preference |
| `C` | Program, author, dependencies and BuyCoffee information | Terminal |
| `Q` | Exit | — |

The startup screen shows the number of `.kzb` files and `.atlas` files found in `input/`.

## Custom assets

First use option `2` or `3`. Copy only the assets you want to modify into `custom/<KZB stem>/`, preserving **the extracted relative paths and numbered filenames**:

```text
input/
  STATUSBAR.kzb
custom/
  STATUSBAR/
    Resource Files/
      Images/
        00036_statusBar_open.png
output/
  custom/
    STATUSBAR_custom.kzb
```

The filename above is an example: use the actual numbered path produced by your extraction. `manifest.json` and `.DS_Store` are ignored. The source KZB is the reference; a manifest is not needed to rebuild. Missing replacements retain the original payload. Unknown paths, symlinks, missing custom folders and empty custom folders are reported as errors. Option `5` replaces existing `Resource Files` only; it does not add objects or change engine `.bin` data, hierarchy or metadata.

PNG/JPEG replacements are structurally checked before rebuilding. Keep the dimensions, colour/alpha requirements and purpose expected by the target application. The toolbox does not determine the semantic compatibility of a replacement.

## Lossless texture optimisation

Option `4` uses the same command-line optimiser families as [ImageOptim](https://github.com/ImageOptim/ImageOptim), without launching the GUI or applying the GUI's possibly lossy preferences:

- PNG: `oxipng`, `optipng`, or `pngcrush`, in that order.
- JPEG: `jpegtran` or `jpegoptim`, in that order. No quality reduction, progressive flag or metadata stripping is requested.

Dorothy searches `PATH` and common locations inside `/Applications/ImageOptim.app` or `~/Applications/ImageOptim.app`, including `ImageOptimGPL.framework`. Tools are **not bundled or downloaded automatically**. Without the required optimiser, that KZB is skipped with an installation message; all other menu tasks remain available.

Example standalone installation on macOS:

```sh
brew install oxipng jpeg-turbo
```

For a keg-only JPEG installation, add its `bin/` directory to your `PATH`. On Debian/Ubuntu, `optipng` and `libjpeg-turbo-progs` provide suitable alternatives. Consult the package manager for your platform.

Textures are extracted to temporary work files under `output/`, optimised, and inserted into a new container only if smaller. PNG CRCs, sample data, colour format and non-IDAT chunks are compared, preserving alpha and metadata. Candidates that change these are discarded. JPEG optimisation uses lossless tools and checks marker framing/dimensions; Dorothy does not implement a full JPEG entropy/pixel decoder. A new `_optimised.kzb` is still produced when no texture gets smaller; the summary then reports zero bytes saved.

## Integrity checks and supported formats

Observed MIB2 (`mib_observed`) and VC (`vc_observed`) header profiles use the same indexed KZB container layout. Checks include file size, recursive directory lengths, object counts, absolute offsets, payload bounds, metadata spans and the end of the last block. Validation also works when Python is launched with `-O`.

For file resources, integrity checks include PNG chunk CRCs, zlib streams and scanline filters, JPEG marker framing, font table bounds and ZIP/XLSX directory recognition. Empty source resources are warnings (the original `MATERIAL_LIBRARY` sample contains a zero-byte XLSX). Engine object semantics, complete JPEG decoding, mesh UV correctness and runtime behaviour are outside these checks. Image inspection has a 256 MiB decoded-PNG limit per resource.

KZB output retains the original index, types, names, metadata and references, updating payload sizes, absolute offsets and file length. These are runtime exports, not editable Kanzi Studio projects. Validation does not prove a modified file will run on a particular MIB/VC firmware. Keep original backups and test on your intended target.

## Virtual Cockpit atlases

Place `.atlas` and same-stem `.json` files directly in `input/`, then choose `A`. The observed `TC/BIN` v4 format stores interleaved LA or RGBA pixels and UID-indexed image rectangles. JSON supplies names, not pixels. Without JSON, images use UID names. Output contains `atlas.png`, named `images/`, `manifest.json` and the preserved `maxrect.bin` trailer. Alpha is preserved. Other atlas layouts are rejected instead of guessed.

## Pulling originals from MIB

Option `P` asks for a hostname/IPv4 address, SSH username, port, an absolute remote directory and whether to use legacy SCP. It invokes local OpenSSH `scp` to read `*.kzb` from that directory (non-recursively). It never uploads files or modifies the remote device. Credentials are handled by SSH in the terminal and are not saved by Dorothy. Normal SSH host-key checks remain enabled.

QNX systems without SFTP commonly require the [legacy `scp -O` option](https://man.openbsd.org/scp). This mode is selectable and defaults to enabled. Enter the actual directory for your unit; no firmware path is assumed. For compatibility with the legacy remote shell, paths with spaces or shell metacharacters are rejected. Use existing SSH agent/key configuration for custom authentication. Failures may leave partial downloads in the allocated output folder.

Downloads remain in `output/pulled/`. **Copy them into `input/` yourself** before using the other tasks. This preserves the rule that Dorothy does not write into `input/`.

## Languages and program information

English is the initial language. All menu labels, messages and prompts live in `config/lang/en.json`. To add a translation:

1. Copy `en.json` to a new file such as `pl.json`.
2. Set `language_name` and translate the values, retaining JSON keys and placeholders such as `{count}` and `{path}`.
3. Select it with `L`. Missing keys fall back to English; malformed translations are ignored with a message.

`config/about.json` contains author, version, repository and coffee URL. The ASCII banner is in `config/banner.txt`. Low-level diagnostics from format validation, Python, SSH or external optimisers may remain in English.

## Non-interactive use and tests

The interactive menu is the default. For scripts or testing:

```sh
python3 dorothy.py --task 1
python3 dorothy.py --task 2 --language en
python3 dorothy.py --root /path/to/workspace --task 5
python3 -m unittest discover -s tests -v
```

A custom workspace must contain the same `config/` structure. Single-task mode returns `0` on success and non-zero on an empty batch, failure or interruption. Tests generate their own redistributable KZB/image fixtures; no vehicle binaries are included. CI runs the standard-library test suite on macOS and Linux.

## Too good to be free?

Coffee-powered development ☕ [BuyCoffee](https://buycoffee.to/mrfix)

Author: mrfix. Unofficial project, not affiliated with Volkswagen Group or Rightware.

## References and third-party software

- [ImageOptim and its underlying tools](https://github.com/ImageOptim/ImageOptim)
- [OxiPNG](https://github.com/oxipng/oxipng), [OptiPNG](https://optipng.sourceforge.net/), [pngcrush](https://pmt.sourceforge.io/pngcrush/)
- [jpegtran/MozJPEG](https://github.com/mozilla/mozjpeg), [jpegoptim](https://github.com/tjko/jpegoptim)
- [OpenSSH SCP](https://man.openbsd.org/scp)
- [ASCII art tool](https://patorjk.com/software/taag/)

Dorothy imports only Python's standard library. Optional external executables retain their own licences; none are redistributed here. Repository inputs, custom assets, generated outputs and local settings are excluded from Git.

## Remaining work & TODOs

- Test rebuilt files on real MIB/VC hardware.
- Configure `https://dorothy.mib-helper.com` redirection to this repository.
- `.canim` MHIG and MHI2 startup images support
- `.boot` MST2 startup images support
- `.mcf` VW/Seat/Skoda HMI assets support
- `.cff` navigation assets support
- `.mxf` MapConfig support
- dividing `input/` folder into file types
- pulling original assets with `scp` into input folder
- pushing modified assets with `scp` back into MIB/QNX
- default credentials for MIB/QNX
- default directories for assets in MIB/QNX
- add version number under the banner
- add line separators for clearer view
- add CLI colors
