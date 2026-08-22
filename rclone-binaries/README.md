# rclone binaries

One rclone binary per RPM build target. The .pro file picks the matching
one automatically at build time via QT_ARCH - no manual copying needed.

| File | Build target | [Download](https://downloads.rclone.org/)|
|---|---|---|
| `rclone-aarch64` | aarch64 (Fairphone 4) | `rclone-vX.Y.Z-linux-arm64.zip` |
| `rclone-armv7hl` | armv7hl (32-bit ARM) | `rclone-vX.Y.Z-linux-arm-v7.zip` |
| `rclone-i486` | i486 (emulator) | `rclone-vX.Y.Z-linux-386.zip` |

To update rclone: download all three zips, extract the single `rclone`
file from each and replace the files above (keep the names). Verify with
`file rclone-*`: aarch64 = ELF 64-bit ARM, armv7hl = ELF 32-bit ARM,
i486 = ELF 32-bit Intel 80386.

Current version: rclone v1.74.3. rclone is MIT-licensed; the license
notice ships with the package.
