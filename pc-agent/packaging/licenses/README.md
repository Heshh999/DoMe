# Licence texts shipped with DoMe for Windows

`build_installer.py` copies every `*.txt` file directly in this folder into `dist/DoMe/licenses/`, and Setup
installs them next to `THIRD_PARTY_NOTICES.txt` (which says which file covers which part). This README is not shipped.

| File | Covers | Where the text came from (2026-10-22) |
| --- | --- | --- |
| `LGPL-2.1.txt` | FFmpeg libraries in `_internal\av.libs` (Screen view builds only) | Debian `base-files` `/usr/share/common-licenses/LGPL-2.1`, SHA-256 `dc626520…fe551`, the same bytes as gnu.org's `lgpl-2.1.txt` |
| `GPL-3.0.txt` | `libgcc_s_seh-1.dll` (with the exception below) | Debian `/usr/share/common-licenses/GPL-3`, SHA-256 `3972dc97…b36986` (gnu.org `gpl-3.0.txt`) |
| `GCC-RUNTIME-LIBRARY-EXCEPTION-3.1.txt` | `libgcc_s_seh-1.dll` | `COPYING.RUNTIME` from the GCC source (github.com/gcc-mirror/gcc, master) |
| `libvpx-LICENSE.txt`, `libvpx-PATENTS.txt` | `libvpx-*.dll` (VP8) | github.com/webmproject/libvpx, main |
| `opus-COPYING.txt` | `libopus-*.dll` | github.com/xiph/opus, main |
| `winpthreads-COPYING.txt` | `libwinpthread-1.dll` | github.com/mingw-w64/mingw-w64, `mingw-w64-libraries/winpthreads/COPYING`, master |
| `Apache-2.0.txt` | OpenSSL 3 (`libcrypto-3.dll`, `libssl-3.dll`, part of the Python runtime) | Debian `/usr/share/common-licenses/Apache-2.0` (apache.org `LICENSE-2.0.txt`) |

`python/` is not copied: `bundle-notices` takes `python/<package>.txt` for a Python package that ships no licence
file of its own (today only `dnspython.txt`: dnspython 2.9.0's wheel has none; the text is `LICENSE` from
github.com/rthalley/dnspython at tag v2.9.0).

The native-library texts were taken from upstream's main branch, not from the exact MSYS2 packages that
`.github/workflows/ffmpeg-lgpl.yml` builds against. When that workflow first runs, compare them with
`/mingw64/share/licenses/<package>/` on the runner (its `pacman -Qi` record names the versions) and replace any
that differ. The licences of the Python packages are not here: `licence_gate.py bundle-notices` copies each
package's own licence files from its installed metadata at build time.
