# Dorothy v0.2.0

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

Unofficial CLI toolbox for Kanzi Studio and startup animation files from VAG MIB2 and Virtual Cockpit.

## Quick start

1. Download or clone the **whole repository**; keep `dorothy.py`, `dorothy_lib/` and `config/` together.
2. Install Python **3.10+**. There are no pip dependencies.
3. Place originals in the prepared input subfolders, keeping atlas/JSON pairs together:

```text
input/
  kzb/
    MIB/STATUSBAR.kzb
    VC/AU491_Logo.kzb
  canim/
    Audi/A3_3_Tuerer_NavigationPlus.canim
    TT/AudiMIBHigh_SUA_A3.canim
  atlas/
    atlas_full_LA.atlas
    atlas_full_LA.json
```

4. Run `python3 dorothy.py`, choose a shortcut and press ENTER.

The workspace lives next to `dorothy.py` regardless of the terminal's current directory. Scanning is recursive, including brand/model subfolders. Formats are selected by extension rather than folder name. Existing files directly in `input/` remain supported. An atlas and matching same-stem JSON count as two physical input files but **one processing job**; the JSON is validated with the atlas. Extension matching is case-insensitive. Missing JSON uses UID asset names. Orphan JSON, unsupported files and malformed inputs are reported, not silently marked valid. Hidden files are ignored; symbolic input/custom paths are not supported.

**Runtime operations never write to `input/`.** All generated files go to `output/`; language preferences go to `config/settings.json`. The repository includes empty folder placeholders only, never vehicle assets. `R` rescans the input; completed tasks return to the menu. Existing outputs are preserved by numbered output folders. Ctrl+C interrupts a task; EOF exits.

## Unified menu

The version appears below the banner. The startup screen shows physical file and processing-job counts.

| Shortcut | Task |
|---|---|
| `1` | Check integrity of input files (dry run, no generated files) |
| `2` | Extract Resource Files (PNG, JPG, TTF...) |
| `3` | Extract All Assets Files |
| `4` | Optimise Textures inside Files |
| `5` | Build custom files with provided assets |
| `P` | Pull `.kzb` files directly from MIB using SCP |
| `R` | Refresh input scan |
| `L` | Change language |
| `C` | Program, author, dependencies and BuyCoffee information |
| `Q` | Show BuyCoffee information and exit |

All numbered actions process the supported input jobs in one batch. Errors are reported per job and do not stop the rest. The former separate `A` atlas shortcut has been removed.

## Resource extraction versus full extraction

Outputs preserve a **format + relative folder + source stem** identity, preventing collisions across formats or vehicle folders. For example, `input/canim/TT/AudiMIBHigh_SUA_A3.canim` produces `output/resources/canim/TT/AudiMIBHigh_SUA_A3/` or `output/assets/canim/TT/AudiMIBHigh_SUA_A3/`.

| Format | Option 2: resources | Option 3: all assets/data |
|---|---|---|
| KZB | Only indexed `Resource Files/` (PNG, JPG, TTF, generic resources) + manifest | All indexed engine objects/resources + manifest |
| CANIM | `img_<ID>.png` textures + manifest | Textures, `commands.bin`, `header.bin`, decoded `container.anim` and manifest |
| Atlas + JSON | Named PNGs under `images/` + manifest | PNGs plus `atlas.png`, `header.bin`, `maxrect.bin`, original `mapping.json` when available and manifest |

PNG alpha is preserved. Interrupted extraction may leave partial output. Generated container files are checked before being published to their final path.

## Custom builds

Use the same source identity under `custom/`. Copy only modified resources, preserving extracted paths and numbered filenames:

```text
input/kzb/MIB/STATUSBAR.kzb
custom/kzb/MIB/STATUSBAR/Resource Files/Images/00036_statusBar_open.png

input/canim/Audi/START.canim
custom/canim/Audi/START/img_1.png

input/atlas/atlas_full_RGBA.atlas
input/atlas/atlas_full_RGBA.json
custom/atlas/atlas_full_RGBA/images/telltale/followStreet.png
```

Names above are examples; use the exact paths and dimensions from your extraction. Missing replacements keep original data. The source is the reference; manifests are not needed for reconstruction. Known metadata files from full exports are ignored; they are not editable animation/packing instructions. Unknown asset paths, symlinks and missing/empty replacement folders are errors. Zero-texture CANIM can be rebuilt from its metadata-only export. The v0.1 `custom/<stem>/` convention remains supported when the stem is unambiguous.

Results go to `output/custom/<source identity>/<stem>_custom.<extension>`. Atlas builds also copy the original mapping to the matching `_custom.json` filename.

- **KZB:** replaces existing `Resource Files`, retaining engine hierarchy, object types and metadata. Recalculates file size, payload sizes and offsets. No adding/removing objects or editing engine `.bin` data.
- **CANIM:** supports the observed `ANIM1` variants, grayscale/RGB/RGBA textures and both image-declaration opcodes `0x11` and `0x15` (Audi TT samples). Colour PNGs may promote grayscale buffers to RGB/RGBA; bpp, subsequent offsets and the pixel-buffer size are recalculated. Dimensions and image counts remain fixed. Existing opcodes, bindings and the rest of the animation program are preserved. It does not emulate masks, rotation or other animation instructions; preserving their rendering purpose is the user's responsibility.
- **Atlas:** patches existing image rectangles, retaining dimensions, channel layout, packing coordinates, gutters and the MAXRECT trailer. LA accepts grayscale+alpha; colour replacements require an RGBA atlas. The format is not silently promoted. Edit individual `images/` files; a modified full `atlas.png` is rejected instead of ignored. Shared/conflicting edits are checked. No repacking or adding/removing images.

CANIM and atlas imports support non-interlaced 8-bit L/LA/RGB/RGBA PNGs and indexed PNGs with transparency. Export 16-bit, Adam7 or APNG input as an ordinary 8-bit PNG first. No automatic scaling, colour correction or alpha premultiplication is performed. Runtime compatibility still requires testing on the intended unit.

## Format-specific lossless optimisation

Option `4` handles each format according to how it stores its pixels:

- **KZB:** extracts embedded PNG/JPEG to temporary files inside `output/`, runs lossless image tools and inserts only smaller candidates. PNG sample data, colour layout and non-IDAT metadata are compared. JPEG uses lossless tools and validates marker framing/dimensions. The result is `<stem>_optimised.kzb`.
- **CANIM:** its textures are raw pixels, not PNGs. Dorothy tries several zlib strategies and retains the smallest stream, including the original as a candidate. The entire decoded file — pixels and animation commands — must remain byte-identical. No external optimiser is needed. The result is `<stem>_optimised.canim`; the original stream is retained when nothing gets smaller.
- **Atlas:** its supported layout stores uncompressed pixels and has no compatible compression flag. Dorothy exports and optimises PNG assets in `exported/`, writes a byte-identical validated `<stem>_optimised.atlas` and matching JSON, and clearly reports **zero container bytes saved**. PNG savings are shown separately. Replacing raw pixels with PNG/zlib bytes would corrupt this atlas format and is not attempted.

Outputs are under `output/optimised/<source identity>/`. Only KZB/atlas PNG processing needs an external PNG optimiser. Optional tools are the families used by [ImageOptim](https://github.com/ImageOptim/ImageOptim):

- PNG: `oxipng`, `optipng`, or `pngcrush`.
- JPEG: `jpegtran` or `jpegoptim`.

Dorothy searches `PATH` and common ImageOptim app bundle locations. No tools are bundled or downloaded automatically, no GUI lossy preferences are used, and no JPEG quality reduction is requested. Missing tools produce an actionable error for that file; other jobs continue.

Example macOS installation: `brew install oxipng jpeg-turbo`. Add the JPEG package's `bin/` directory to `PATH` when keg-only. Debian/Ubuntu alternatives include `optipng` and `libjpeg-turbo-progs`.

## Integrity scope

Option `1` is read-only and dispatches by file format:

- **KZB:** observed MIB2/VC header profiles, recursive directory lengths, object counts, payload boundaries, metadata spans and final file size; PNG CRC/zlib/scanline checks, JPEG marker framing, font table bounds and ZIP/XLSX directory recognition. Empty resources are warnings.
- **CANIM:** zlib checksum/end/length, ANIM1 header, command-block bounds, declarations, supported bpp, unique IDs, contiguous non-overlapping image ranges and declared pixel-buffer length. Opcode `0x15` is retained; its complete rendering semantics are not assumed.
- **Atlas/JSON:** TC/BIN headers, UID sets, dimensions, integer UV rectangles, buffer bounds, safe output names and the MAXRECT trailer signature. Metadata is checked even though it is not a separate batch job.

This does not prove runtime compatibility, decode all JPEG pixels, validate every animation opcode or fully interpret the MAXRECT trailer. Rebuilt files have not been tested on real MIB/VC hardware. Keep originals. Decoded-image/CANIM inspection limits apply as documented in the code; these are toolbox safeguards, not device RAM limits.

## Pulling originals from MIB

`P` asks for a hostname/IPv4 address, SSH user, port, absolute remote folder and legacy SCP preference. It reads `*.kzb` non-recursively with local OpenSSH and saves them under `output/pulled/<host>/`. Normal host-key checks remain enabled, and credentials are handled by SSH without storage in Dorothy. Legacy [SCP `-O`](https://man.openbsd.org/scp) is selectable for QNX systems without SFTP. Paths with spaces/shell metacharacters are rejected.

Copy downloads into `input/kzb/` yourself; Dorothy never changes `input/` or uploads to the vehicle. Partial downloads may remain after a failed transfer.

## Languages and program information

English is the initial language. Labels, menus, prompts and messages are in `config/lang/en.json`. Copy it to e.g. `pl.json`, translate values and `language_name`, retaining keys and placeholders. Select it with `L`. Missing keys fall back to English; invalid translations are reported. Existing translations should update their menu arrays for v0.2.0.

`config/about.json` defines author, version, repository and coffee URL; `config/banner.txt` holds the ASCII art. Low-level diagnostics from parsers, Python, SSH or external tools may remain in English. Both `C` and explicit `Q` show the BuyCoffee URL.

## Non-interactive use and tests

```sh
python3 dorothy.py --task 1
python3 dorothy.py --task 2 --language en
python3 dorothy.py --root /path/to/workspace --task 5
python3 -m unittest discover -s tests -v
```

Single-task mode returns non-zero on an empty batch, unsupported/failed input or interruption. A custom workspace needs the same `config/` structure. Tests generate their own redistributable fixtures for mixed-format batches, colour promotion, offsets, JSON pairing, readonly inputs, output collisions, optimisation, menus, version and exit behaviour. No vehicle binaries are included in the repository. CI runs on macOS/Linux.

## v0.2.0 changes

- Prepared `input/kzb/`, `input/canim/`, `input/atlas/` with recursive discovery.
- Unified integrity, resource/full extraction, optimisation and custom build menu actions.
- Integrated CANIM support, including Audi TT declarations and grayscale-to-colour rebuilding.
- Integrated atlas/JSON validation and custom atlas builds; removed the separate `A` action.
- Version under the banner; BuyCoffee on `Q`; version bumped to `0.2.0`.

## Too good to be free?

Coffee-powered development ☕ [BuyCoffee](https://buycoffee.to/mrfix)

Author: mrfix. Unofficial project, not affiliated with Volkswagen Group or Rightware.

## References and third-party software

- [ImageOptim and its tools](https://github.com/ImageOptim/ImageOptim)
- [OxiPNG](https://github.com/oxipng/oxipng), [OptiPNG](https://optipng.sourceforge.net/), [pngcrush](https://pmt.sourceforge.io/pngcrush/)
- [jpegtran/MozJPEG](https://github.com/mozilla/mozjpeg), [jpegoptim](https://github.com/tjko/jpegoptim)
- [OpenSSH SCP](https://man.openbsd.org/scp)
- [MIB2 Toolbox CANIM format reference](https://github.com/jilleb/mib2-toolbox/tree/master/Tools)
- [ASCII art tool](https://patorjk.com/software/taag/)

Dorothy imports only Python's standard library. Optional third-party executables keep their own licences and are not redistributed. Local inputs, modifications, outputs and settings are excluded from Git.

## Remaining work

- Test rebuilt files on real MIB/VC hardware.
- Configure `https://dorothy.mib-helper.com` redirection to this repository.
