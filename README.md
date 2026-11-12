# GEF fork for WCH CH32V003 (RV32EC)

> Personal, experimental fork of [hugsy/gef](https://github.com/hugsy/gef),
> pinned to upstream tag [`2024.06`](https://github.com/hugsy/gef/tree/2024.06).
> It is not affiliated with, or supported by, upstream GEF.

## Why this fork exists

This fork exists to debug the **WCH CH32V003** (QingKe V2, RV32EC) over the
GDB stub in [minichlink](https://github.com/cnlohr/ch32fun), using the WCH
`riscv-wch-elf-gdb` toolchain.

Stock GEF does not work well in that environment:

1. **Python version.** The WCH toolchain embeds Python 3.8. GEF 2025.01 and
   later require Python 3.10, so this fork stays on the 2024.06 code base.
2. **No OS memory map.** GEF's `registers`, `dereference` and `stack` view need
   a memory layout. On the MCU there is no `/proc`, `info proc mappings` fails,
   and minichlink does not implement `monitor info mem`.
3. **RV32E registers.** GEF advertises the full RV32I register file
   (`$a6`, `$a7`, `$s2`...), but the CH32V003 only has 16 registers.
4. **Compressed instructions.** GEF assumes fixed 4-byte RISC-V instructions
   and computes "N instructions before PC" as `pc - 4*N`. That skips 2-byte
   C-extension instructions and makes the code pane drift while single-stepping.
5. **Source colours.** GEF renders its source pane itself, so GDB's Pygments
   source styling does not apply there.

Most changes live in **`gef_riscv.py`**, a companion loaded after `gef.py`.
Keeping them outside `gef.py` makes it easier to rebase on upstream. A few
small, self-contained fixes are backported directly into `gef.py`:

- RISC-V `bge`/`bgeu` taken-condition fix.
- `TIOCGWINSZ` 8-byte buffer fix.
- `Section.is_readable/is_writable/is_executable` truthiness fix.
- Cap on the distance searched when `context` guesses function arguments.

## What `gef_riscv.py` adds

- A bare-metal memory map derived from the loaded ELF:
  - `0x00000000-0x00004000` flash, `r-x`
  - `0x20000000-0x20000800` SRAM, `rw-`
  - `0x40000000-0x40030000` APB peripherals, `rw-`
  - `0xE0000000-0xE0001000` core peripherals, `rw-`
- Filtering of GEF's register list to the 16 real RV32E registers.
- Variable-length instruction lookup, so 2-byte and 4-byte instructions are
  both handled correctly in the code pane.
- Unsigned decimal values alongside the hex in `registers` and the context view.
- Pygments/GAS syntax colouring for the source pane.
- An embedded context profile: `regs stack code source`, stack growing down,
  no screen clear, and no source-variable values.

The profile is applied every time the companion loads. Override any setting
afterwards if you want the full layout back, for example:

```gdb
gef config context.layout "legend regs stack code args source memory threads trace extra"
```

## Usage

Clone this branch:

```bash
git clone --branch ch32v003-rv32ec https://github.com/yfozekosh/gef.git
```

Load GEF, then the companion:

```gdb
python import os; os.environ.pop("PYTHONHOME", None)
source ~/Projects/gef/gef.py
python import os, runpy; runpy.run_path(os.path.expanduser("~/Projects/gef/gef_riscv.py"))
```

`PYTHONHOME` is removed because the WCH GDB exports its embedded Python home;
GEF's internal `python3` probe fails if it inherits that value.

To make this repeatable, add a command such as:

```gdb
define gef
    python import os; os.environ.pop("PYTHONHOME", None)
    source ~/Projects/gef/gef.py
    python import os, runpy; runpy.run_path(os.path.expanduser("~/Projects/gef/gef_riscv.py"))
end

document gef
    Load GEF and the CH32V003/RV32EC companion.
end
```

After GEF loads, `gef` becomes GEF's own help command. Use a separate command
if you need to re-apply the companion without reloading GEF:

```gdb
define gef-riscv
    python import os, runpy; runpy.run_path(os.path.expanduser("~/Projects/gef/gef_riscv.py"))
end
```

## Scope and limitations

- The memory map is sized for the **CH32V003F4P6**: 16 KiB flash, 2 KiB SRAM.
  Adjust the constants at the top of `gef_riscv.py` for another part.
- GEF's Linux-userland commands (`vmmap`, `heap`, and similar) are not useful
  on a bare-metal target.
- Tested with WCH GDB 17.1 (embedded Python 3.8.20), minichlink, and
  `riscv-wch-elf-gdb`; not every GEF command has been validated on hardware.
- Upstream GEF is not responsible for these changes. Report fork-specific
  issues at <https://github.com/yfozekosh/gef/issues>.

## License

MIT, inherited from GEF. See [`LICENSE`](LICENSE).

---

The upstream GEF README follows unchanged.

<p align="center">
  <img src="https://i.imgur.com/o0L8lPN.png" alt="logo"/>
</p>

<p align="center">
    <a href="https://discord.gg/hSbqxxBgRX"><img alt="Discord" src="https://img.shields.io/badge/Discord-BlahCats-yellow"></a>
  <a href="https://hugsy.github.io/gef"><img alt="Docs" src="https://img.shields.io/badge/Docs-gh--pages-brightgreen"></a>
  <a title="Use the IDs: gef/gef-demo" href="https://demo.gef.blah.cat"><img alt="Try GEF" src="https://img.shields.io/badge/Demo-Try%20GEF%20Live-blue"></a>
</p>

`GEF` (pronounced ʤɛf - "Jeff") is a set of commands for x86/64, ARM, MIPS, PowerPC and SPARC to
assist exploit developers and reverse-engineers when using old school GDB. It provides additional
features to GDB using the Python API to assist during the process of dynamic analysis and exploit
development. Application developers will also benefit from it, as GEF lifts a great part of regular
GDB obscurity, avoiding repeating traditional commands, or bringing out the relevant information
from the debugging runtime.

## Instant Setup

Simply make sure you have [GDB 8.0 or higher](https://www.gnu.org/s/gdb) compiled with Python3.6+
bindings, then:

```bash
# via the install script
## using curl
$ bash -c "$(curl -fsSL https://gef.blah.cat/sh)"

## using wget
$ bash -c "$(wget https://gef.blah.cat/sh -O -)"

# or manually
$ wget -O ~/.gdbinit-gef.py -q https://gef.blah.cat/py
$ echo source ~/.gdbinit-gef.py >> ~/.gdbinit

# or alternatively from inside gdb directly
$ gdb -q
(gdb) pi import urllib.request as u, tempfile as t; g=t.NamedTemporaryFile(suffix='-gef.py'); open(g.name, 'wb+').write(u.urlopen('https://tinyurl.com/gef-main').read()); gdb.execute('source %s' % g.name)
```

You can immediately see that GEF is correctly installed by launching GDB:

![gef-context](https://i.imgur.com/E3EuQPs.png)

A few of `GEF` features include:

*  **One** single GDB script
*  Entirely **architecture agnostic**, **NO** dependencies: `GEF` is battery-included and [is
  installable instantly](https://hugsy.github.io/gef/#setup)
*  **Fast** limiting the number of dependencies and optimizing code to make the commands as fast as
  possible
*  Provides a great variety of commands to drastically change your experience in GDB.
*  [**Easily** extensible](https://hugsy.github.io/gef/api/) to create other commands by providing
d more comprehensible layout to GDB Python API.
*  Full Python3 support ([Python2 support was
  dropped](https://github.com/hugsy/gef/releases/tag/2020.03) - see
  [`gef-legacy`](https://github.com/hugsy/gef-legacy)).
*  Built around an architecture abstraction layer, so all commands work in any GDB-supported
  architecture such as x86-32/64, ARMv5/6/7, AARCH64, SPARC, MIPS, PowerPC, etc.
* Suited for real-life apps debugging, exploit development, just as much as CTF
* And a lot more commands contributed by the community available on
  [GEF-Extras](https://github.com/hugsy/gef-extras) !!

Check out the [Screenshot page](docs/screenshots.md) for more or [try it
online](https://demo.gef.blah.cat) (user:`gef`/password:`gef-demo`)

## Documentation

Unlike other GDB plugins, GEF has an extensive and up-to-date
[documentation](https://hugsy.github.io/gef/). Users are recommended to refer to it as it may help
them in their attempts to use GEF. In particular, new users should navigate through it (see the
[FAQ](https://hugsy.github.io/gef/faq/) for common installation problems), and the problem persists,
try to reach out for help on the Discord channel or submit an issue.

## Current status

| Documentation |License | Compatibility | CI Tests (`main`) |
|:---:|:---:|:---|--|
| [![Documentation](https://github.com/hugsy/gef/actions/workflows/generate-docs.yml/badge.svg)](https://github.com/hugsy/gef/actions/workflows/generate-docs.yml) | [![MIT](https://img.shields.io/packagist/l/doctrine/orm.svg?maxAge=2592000?style=plastic)](https://github.com/hugsy/gef/blob/main/LICENSE) | [![Python 3](https://img.shields.io/badge/Python-3-green.svg)](https://github.com/hugsy/gef/) | [![CI Test for GEF](https://github.com/hugsy/gef/actions/workflows/run-tests.yml/badge.svg)](https://github.com/hugsy/gef/actions/workflows/run-tests.yml) |

## Contribute

To get involved, refer to the [Contribution
documentation](https://hugsy.github.io/gef/#contribution) and the
[guidelines](https://github.com/hugsy/gef/blob/main/.github/CONTRIBUTING.md) to start.

## Sponsors

Another way to contribute to keeping the project alive is by sponsoring it! Check out [the
sponsoring documentation](https://hugsy.github.io/gef/#sponsors) for details so you can be part of
the list of those [awesome sponsors](https://github.com/sponsors/hugsy).

## Happy Hacking 🍻
