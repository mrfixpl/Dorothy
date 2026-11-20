# Dorothy v0.3.0

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

Unofficial CLI toolbox for Kanzi Studio and startup animation files from VAG MIB2 (including MST2 BOOT) and Virtual Cockpit.

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
  boot/
    MST2/startup_1.boot
  atlas/
    atlas_full_LA.atlas
    atlas_full_LA.json
```

4. Run `python3 dorothy.py`, choose a shortcut and press ENTER.

The workspace lives next to `dorothy.py` regardless of the terminal's current directory. Scanning is recursive, including brand/model subfolders. Formats are selected by extension rather than folder name. Existing files directly in `input/` remain supported. An atlas and matching same-stem JSON count as two physical input files but **one processing job**; the JSON is validated with the atlas. Extension matching is case-insensitive. Missing JSON uses UID asset names. Orphan JSON, unsupported files and malformed inputs are reported, not silently marked valid. Hidden files are ignored; symbolic input/custom paths are not supported.

**Processing tasks 1–5 never write to `input/`.** Generated assets go to `output/`; language preferences go to `config/settings.json`. The explicit `A` archive/cleanup action saves a verified backup in `archives/` before removing session files from `input/`, `output/` and `custom/`. The repository includes empty folder placeholders only, never vehicle assets. `R` rescans the input; completed tasks return to the menu. Existing outputs are preserved by numbered output folders. Ctrl+C interrupts a task; EOF exits.

## Unified menu

The version appears below the banner. The startup screen shows physical file and processing-job counts, with separate counters for KZB, CANIM, BOOT, ATLAS, JSON and other files. JSON companions are counted physically but paired with their atlas as one job.

| Shortcut | Task |
|---|---|
| `1` | Check integrity of input files (dry run, no generated files) |
| `2` | Extract Resource Files (PNG, JPG, TTF...) |
| `3` | Extract All Assets Files |
| `4` | Optimise Textures inside Files |
| `5` | Build custom files with provided assets |
| `A` | Archive & Clean-up: verified ZIP backup, then clear session files |
| `P` | Pull `.kzb` files directly from MIB using SCP |
| `R` | Refresh input scan |
| `L` | Change language |
| `C` | Program, author, dependencies and BuyCoffee information |
| `Q` | Show BuyCoffee information and exit |

All numbered actions process the supported input jobs in one batch. Errors are reported per job and do not stop the rest. The `A` shortcut now archives the session; atlas processing remains part of the numbered actions.

## Resource extraction versus full extraction

Outputs preserve a **format + relative folder + source stem** identity, preventing collisions across formats or vehicle folders. For example, `input/canim/TT/AudiMIBHigh_SUA_A3.canim` produces `output/resources/canim/TT/AudiMIBHigh_SUA_A3/` or `output/assets/canim/TT/AudiMIBHigh_SUA_A3/`.

| Format | Option 2: resources | Option 3: all assets/data |
|---|---|---|
| KZB | Only indexed `Resource Files/` (PNG, JPG, TTF, generic resources) + manifest | All indexed engine objects/resources + manifest |
| CANIM | `img_<ID>.png` textures + manifest | Textures, `commands.bin`, `header.bin`, decoded `container.anim` and manifest |
| BOOT | Editable RGB PNGs under `images/` + manifest | RGB PNGs, native PNG-carrier `.mib` files under `raw/`, exact compressed image records, header/commands/MIFA/offset/trailer binaries, command CSV and manifest |
| Atlas + JSON | Named PNGs under `images/` + manifest | PNGs plus `atlas.png`, `header.bin`, `maxrect.bin`, original `mapping.json` when available and manifest |

PNG alpha is preserved. Interrupted extraction may leave partial output. Generated container files are checked before being published to their final path.

## Custom builds

Use the same source identity under `custom/`. Copy only modified resources, preserving extracted paths and numbered filenames:

```text
input/kzb/MIB/STATUSBAR.kzb
custom/kzb/MIB/STATUSBAR/Resource Files/Images/00036_statusBar_open.png

input/canim/Audi/START.canim
custom/canim/Audi/START/img_1.png

input/boot/MST2/startup_1.boot
custom/boot/MST2/startup_1/images/img_00.png

input/atlas/atlas_full_RGBA.atlas
input/atlas/atlas_full_RGBA.json
custom/atlas/atlas_full_RGBA/images/telltale/followStreet.png
```

Names above are examples; use the exact paths and dimensions from your extraction. Missing replacements keep original data. The source is the reference; manifests are not needed for reconstruction. Known metadata files from full exports are ignored; they are not editable animation/packing instructions. Unknown asset paths, symlinks and missing/empty replacement folders are errors. Zero-texture CANIM and BOOT can be rebuilt from its metadata-only export. The v0.1 `custom/<stem>/` convention remains supported when the stem is unambiguous.

Results go to `output/custom/<source identity>/<stem>_custom.<extension>`. Atlas builds also copy the original mapping to the matching `_custom.json` filename.

- **KZB:** replaces existing `Resource Files`, retaining engine hierarchy, object types and metadata. Recalculates file size, payload sizes and offsets. No adding/removing objects or editing engine `.bin` data.
- **CANIM:** supports the observed `ANIM1` variants, grayscale/RGB/RGBA textures and both image-declaration opcodes `0x11` and `0x15` (Audi TT samples). Colour PNGs may promote grayscale buffers to RGB/RGBA; bpp, subsequent offsets and the pixel-buffer size are recalculated. Dimensions and image counts remain fixed. Existing opcodes, bindings and the rest of the animation program are preserved. It does not emulate masks, rotation or other animation instructions; preserving their rendering purpose is the user's responsibility.
- **BOOT:** supports the observed MST2 `SCRT` version 101/variant 1 with `MIFA` image tables. Replace `images/img_XX.png` with same-size opaque RGB PNGs, including colour replacements for grayscale-looking images. The native packed format shares colour information between pixel pairs: importing edited RGB can quantise colours. Unchanged exported RGB preserves the original native bytes exactly. Alternatively, use `raw/img_XX.mib` for exact native replacement; these PNG carriers encode packed channels, not visible grayscale/alpha. A full export may be copied into `custom/`: unchanged alternatives do not override edited images; conflicting edits to both representations are rejected. Header, animation program and padding are preserved; offsets and lengths are recalculated. Binary metadata is not an animation editor, and modified `records/*.bin` are rejected. No adding/removing/resizing images.
- **Atlas:** patches existing image rectangles, retaining dimensions, channel layout, packing coordinates, gutters and the MAXRECT trailer. LA accepts grayscale+alpha; colour replacements require an RGBA atlas. The format is not silently promoted. Edit individual `images/` files; a modified full `atlas.png` is rejected instead of ignored. Shared/conflicting edits are checked. No repacking or adding/removing images.

CANIM, BOOT and atlas imports support non-interlaced 8-bit L/LA/RGB/RGBA PNGs and indexed PNGs with transparency. Export 16-bit, Adam7 or APNG input as an ordinary 8-bit PNG first. No automatic scaling, colour correction or alpha premultiplication is performed. Runtime compatibility still requires testing on the intended unit.

## Format-specific lossless optimisation

Option `4` handles each format according to how it stores its pixels:

- **KZB:** extracts embedded PNG/JPEG to temporary files inside `output/`, runs lossless image tools and inserts only smaller candidates. PNG sample data, colour layout and non-IDAT metadata are compared. JPEG uses lossless tools and validates marker framing/dimensions. The result is `<stem>_optimised.kzb`.
- **CANIM:** its textures are raw pixels, not PNGs. Dorothy tries several zlib strategies and retains the smallest stream, including the original as a candidate. The entire decoded file — pixels and animation commands — must remain byte-identical. No external optimiser is needed. The result is `<stem>_optimised.canim`; the original stream is retained when nothing gets smaller.
- **BOOT:** recompresses each native image zlib stream, retaining the original when smaller. Native pixels, image dimensions, animation commands and padding remain unchanged; RGB conversion is not part of optimisation. No external optimiser is needed. The result is `<stem>_optimised.boot`.
- **Atlas:** its supported layout stores uncompressed pixels and has no compatible compression flag. Dorothy exports and optimises PNG assets in `exported/`, writes a byte-identical validated `<stem>_optimised.atlas` and matching JSON, and clearly reports **zero container bytes saved**. PNG savings are shown separately. Replacing raw pixels with PNG/zlib bytes would corrupt this atlas format and is not attempted.

Outputs are under `output/optimised/<source identity>/`. Only KZB/atlas PNG processing needs an external PNG optimiser. Optional tools are the families used by [ImageOptim](https://github.com/ImageOptim/ImageOptim):

- PNG: `oxipng`, `optipng`, or `pngcrush`.
- JPEG: `jpegtran` or `jpegoptim`.

Dorothy searches `PATH` and common ImageOptim app bundle locations. No tools are bundled or downloaded automatically, no GUI lossy preferences are used, and no JPEG quality reduction is requested. Missing tools produce an actionable error for that file; other jobs continue.

Example macOS installation: `brew install oxipng jpeg-turbo`. Add the JPEG package's `bin/` directory to `PATH` when keg-only. Debian/Ubuntu alternatives include `optipng` and `libjpeg-turbo-progs`.

## Percentage results and terminal colours

Every optimisation result shows input/output file sizes, saved bytes and the reduction as a percentage of the **original file size**:

```text
reduction = (original_bytes - output_bytes) / original_bytes * 100
```

Percentages are displayed to two decimal places. An unchanged file reports `0.00%`. Raw atlas containers therefore report `0.00%`, while exported PNG savings include their own percentage, based on the combined original PNG size.

ANSI colours distinguish counters, progress, success, warnings and failures. They are enabled on compatible terminals and disabled for redirected output, `TERM=dumb`, or when `NO_COLOR` is set. Text and translation files contain no hardcoded escape codes. Colours do not affect saved files.

## Archive & Clean-up

Choose `A` to start a fresh session. The action includes **all regular files**, including hidden files, under `input/`, `output/` and `custom/`. Directory structure is stored with these three top-level names so the ZIP can be restored into a workspace. Empty directories are included. `archives/`, configuration and the program itself are not included.

The archive uses the local computer's date/time and is saved as:

```text
archives/Dorothy_Archive_2026-11-20_12-34-56.zip
```

Dorothy first writes a temporary ZIP with ZIP64 support, closes and flushes it, then reads every archived member to verify its CRC, length and SHA-256 against the bytes read from the source. It checks that the session file inventory has not changed before publishing the completed archive. Only then does cleanup begin.

Cleanup removes only the exact files already backed up, rechecking their identity/content before deletion. Folder templates and empty `.gitkeep` placeholders are retained; `input/kzb/`, `input/canim/`, `input/boot/`, `input/atlas/`, `output/` and `custom/` remain ready for use. Existing archives are never overwritten: if the same timestamp already exists, retry in the next second. Symbolic links and special files are rejected rather than followed.

If writing or verification fails, no source files are removed. If cleanup fails or is interrupted after publishing, the verified archive is retained and its path is reported; already removed files can be recovered from it, and remaining/new files are not forcibly deleted. This is not a multi-folder atomic transaction. Do not edit the session in another process while archiving. The program never recursively deletes an unverified directory tree.

The action can also be run explicitly without the menu:

```sh
python3 dorothy.py --task A
```

This command performs the same cleanup, so use it only when you intend to close the current session. ZIPs are excluded from Git.

## Integrity scope

Option `1` is read-only and dispatches by file format:

- **KZB:** observed MIB2/VC header profiles, recursive directory lengths, object counts, payload boundaries, metadata spans and final file size; PNG CRC/zlib/scanline checks, JPEG marker framing, font table bounds and ZIP/XLSX directory recognition. Empty resources are warnings.
- **CANIM:** zlib checksum/end/length, ANIM1 header, command-block bounds, declarations, supported bpp, unique IDs, contiguous non-overlapping image ranges and declared pixel-buffer length. Opcode `0x15` is retained; its complete rendering semantics are not assumed.
- **BOOT:** SCRT version/variant, command alignment, draw-image references, MIFA length, ordered image offsets, dimensions, per-image zlib checksums/end/decoded sizes and supported zero padding. Unknown opcodes and missing END are reported as warnings, preserving observed originals.
- **Atlas/JSON:** TC/BIN headers, UID sets, dimensions, integer UV rectangles, buffer bounds, safe output names and the MAXRECT trailer signature. Metadata is checked even though it is not a separate batch job.

This does not prove runtime compatibility, decode all JPEG pixels, validate every animation opcode or fully interpret the MAXRECT trailer. Rebuilt files have not been tested on real MIB/VC hardware. Keep originals. Decoded-image/CANIM inspection limits apply as documented in the code; these are toolbox safeguards, not device RAM limits.

## Pulling originals from MIB

`P` asks for a hostname/IPv4 address, SSH user, port, absolute remote folder and legacy SCP preference. It reads `*.kzb` non-recursively with local OpenSSH and saves them under `output/pulled/<host>/`. Normal host-key checks remain enabled, and credentials are handled by SSH without storage in Dorothy. Legacy [SCP `-O`](https://man.openbsd.org/scp) is selectable for QNX systems without SFTP. Paths with spaces/shell metacharacters are rejected.

Copy downloads into `input/kzb/` yourself; the pull action never changes `input/` or uploads to the vehicle. Partial downloads may remain after a failed transfer.

## Languages and program information

English is the initial language. Labels, menus, prompts and messages are in `config/lang/en.json`. Copy it to e.g. `pl.json`, translate values and `language_name`, retaining keys and placeholders. Select it with `L`. Missing keys fall back to English; invalid translations are reported. Existing translations should update their menu arrays and result placeholders for v0.3.0 (including the `{boot}` input counter and BOOT notices).

`config/about.json` defines author, version, repository and coffee URL; `config/banner.txt` holds the ASCII art. Low-level diagnostics from parsers, Python, SSH or external tools may remain in English. Both `C` and explicit `Q` show the BuyCoffee URL.

## Non-interactive use and tests

```sh
python3 dorothy.py --task 1
python3 dorothy.py --task 2 --language en
python3 dorothy.py --root /path/to/workspace --task 5
python3 -m unittest discover -s tests -v
```

Single-task mode returns non-zero on an empty batch, unsupported/failed input or interruption. A custom workspace needs the same `config/` structure. Tests generate their own redistributable fixtures for mixed-format batches, colour promotion, offsets, JSON pairing, readonly inputs, output collisions, optimisation, menus, version and exit behaviour. No vehicle binaries are included in the repository. CI runs on macOS/Linux. Optional real-file codec checks can be enabled with `DOROTHY_BOOT_CORPUS=/path/to/boot/samples`; these binaries are not bundled.

## v0.3.0 changes

- MST2 `.boot` support across integrity, resource/full extraction, optimisation and custom builds.
- Ordinary editable RGB PNG exports, native `.mib` exports and complete binary container partitions.
- BOOT input counters, recursive discovery, archive cleanup templates and `input/boot/`.
- Reference-preserving custom builds and lossless native-stream recompression.
- Version bumped to `0.3.0`.

## v0.2.1 changes

- File-size reduction percentages, including separate exported-atlas PNG percentages.
- ANSI-coloured counters, statuses and results, with plain-text/NO_COLOR support.
- Separate input counters for each file type.
- `A - Archive & Clean-up`, verified ZIPs under `archives/` and reusable session folders.
- Version bumped to `0.2.1`.

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
- [MST2 Toolbox and mib2image BOOT colour-format reference](https://github.com/olli991/mib-std2-pq-zr-toolbox/tree/master/tools)
- [ASCII art tool](https://patorjk.com/software/taag/)

Dorothy imports only Python's standard library. Optional third-party executables keep their own licences and are not redistributed. Local inputs, modifications, outputs and settings are excluded from Git.

## Remaining work & TODOs

- Test rebuilt files on real MIB/VC hardware.
- Configure `https://dorothy.mib-helper.com` redirection to this repository.
- `.mcf` VW/Seat/Skoda MHI2 assets support
- `.mif` VW/Seat/Skoda MHIG assets support
- `.cff` navigation assets support
- `.mxf` MapConfig support
- pulling original assets with `scp` into input folder
- pushing modified assets with `scp` back into MIB/QNX
- default credentials for MIB/QNX
- default directories for assets in MIB/QNX
- add line separators for clearer view
- pulling `https://github.com/ImageOptim/ImageOptim` as copression tool
- force compress owhen building
