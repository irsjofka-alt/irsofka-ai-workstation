# Third-Party Notices

Source code created in this project is protected by the **Business Source License 1.1** (see
[`LICENSE`](LICENSE)): non-production use is free, production use requires a commercial licence
from the Licensor, and each version automatically transitions to MIT on its specified Change Date.
Certain assets included in distribution are **not** created by this project and remain under their
respective upstream licences. Those licences are more permissive and are deliberately **not** restricted
by `LICENSE`, even after the Change Date of a given version has passed.

## xterm.js — MIT License

| | |
|---|---|
| Files | `engine-rust/assets/vendor/xterm.js`, `engine-rust/assets/vendor/xterm.css` |
| Copyright | © 2014 The xterm.js authors. Early portions © 2012–2013 Christopher Jeffrey |
| Licence | MIT |
| Source | https://github.com/xtermjs/xterm.js |

The copyright header is embedded directly inside `xterm.css` and `xterm.js` and must be preserved.
Full MIT text:

> Permission is hereby granted, free of charge, to any person obtaining a copy of this
> software and associated documentation files (the "Software"), to deal in the Software
> without restriction, including without limitation the rights to use, copy, modify, merge,
> publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons
> to whom the Software is furnished to do so, subject to the following conditions...
>
> (Full text is available at the upstream link above.)

## @xterm/addon-fit — MIT License

| | |
|---|---|
| Files | `engine-rust/assets/vendor/addon-fit.js` |
| Copyright | © The xterm.js authors |
| Licence | MIT |
| Source | https://github.com/xtermjs/xterm.js/tree/master/addons/fit |

## Fonts: Inter & JetBrains Mono — SIL Open Font License 1.1

| | |
|---|---|
| Files | `engine-rust/assets/fonts/inter-*.woff2`, `engine-rust/assets/fonts/jb-*.woff2` |
| Licence | SIL Open Font License 1.1 |
| Licence text | [`engine-rust/assets/fonts/OFL.txt`](engine-rust/assets/fonts/OFL.txt) |
| Source | https://rsms.me/inter/ · https://www.jetbrains.com/mono/ |

OFL requires the licence text to be included whenever fonts are distributed — for this reason,
`OFL.txt` is tracked in the repository. The fonts may not be sold standalone, and the names
"Inter" and "JetBrains Mono" may not be used for modified versions.

## Rust & Python Dependencies

`engine-rust/Cargo.lock` and Python modules reference third-party libraries without bundling their
source. The licenses of individual dependencies follow the respective crates and packages resolved
during build time and remain outside the scope of this project's `LICENSE`.

## Reuse Summary

Permitted actions without additional permissions:

- **Project code**: use, modify, and distribute for **non-commercial** purposes.
- **Vendor assets above**: use, modify, and distribute, including commercially, subject to their
  respective MIT/OFL licences, provided copyright headers and licence notices are preserved.
