# Part of https://github.com/yfozekosh/gef (branch ch32v003-rv32ec).
# MIT licensed; see LICENSE.
"""Bare-metal RISC-V (RV32EC / CH32V003) adjustments for GEF 2024.06.

Loaded after gef.py (see README for the GDB commands). It adds:

  * a memory map provider built from the loaded ELF and the linker's stack
    symbol, so GEF's `registers`, `dereference` and the `stack` context pane
    work over minichlink's remote (there is no /proc on the target, and
    `info proc mappings` therefore fails with "Failed to get memory layout");
  * the RV32E register set (no $a6/$a7/$s2..$s11/$t3..$t6);
  * the unsigned decimal value alongside the hex in `registers`/context;
  * Pygments syntax colouring for the `source` context pane;
  * an embedded context profile: registers, stack, code and source only,
    stack growing down, no screen clear and no source variable values.
"""

import os
import re
import string
import struct

import gdb

# GEF is sourced into GDB's __main__ namespace, not an importable module.
import __main__ as _main

gef = _main.gef
Section = _main.Section
Permission = _main.Permission
ContextCommand = _main.ContextCommand
DetailRegistersCommand = _main.DetailRegistersCommand
Color = _main.Color
gef_print = _main.gef_print
RIGHT_ARROW = _main.RIGHT_ARROW
BP_GLYPH = _main.BP_GLYPH
align_address = _main.align_address
lookup_address = _main.lookup_address
dereference_from = _main.dereference_from
format_address_spaces = _main.format_address_spaces
gef_pystring = _main.gef_pystring
err = _main.err

# --- configuration -------------------------------------------------------

# CH32V003F4P6: 16 KiB flash, 2 KiB SRAM, plus the peripheral windows.
FLASH_START = 0x00000000
FLASH_END = 0x00004000
RAM_START = 0x20000000
RAM_END = 0x20000800
PERIPH_RANGES = (
    (0x40000000, 0x40030000),  # APB1/APB2 (GPIO, USART, SPI, I2C, RCC, ...)
    # PFIC is at 0xE000E000 and SysTick at 0xE000F000, both outside the
    # first 4 KiB of the core-peripheral window.
    (0xE0000000, 0xE0010000),
)

# Bare-metal context profile. GEF's defaults include panes that need a
# userland process (args, memory, threads, trace) and clear the terminal on
# every stop. These four panes are the useful ones on the CH32V003.
EMBEDDED_PROFILE = {
    "context.layout": "regs stack code source",
    "context.grow_stack_down": True,
    "context.clear_screen": False,
    "context.show_source_code_variable_values": False,
}

RV32E_REGS = (
    "$zero", "$ra", "$sp", "$gp", "$tp", "$t0", "$t1", "$t2",
    "$fp", "$s1", "$a0", "$a1", "$a2", "$a3", "$a4", "$a5",
)

Pygments = None
try:
    from pygments import highlight as _pygments_highlight
    from pygments.formatters import Terminal256Formatter
    from pygments.lexers import CLexer, GasLexer

    Pygments = True
except Exception:
    Pygments = False


# --- memory map ----------------------------------------------------------

_SECTION_RE = re.compile(
    r"^\s*\[\d+\]\s+(0x[0-9a-fA-F]+)->(0x[0-9a-fA-F]+)"
    r"(?:\s+at\s+\S+)?:\s+(\S+)\s*(.*)$"
)


def _elf_sections():
    """Allocated sections of the loaded ELF, as GEF Section objects."""
    try:
        out = gdb.execute("maintenance info sections", to_string=True)
    except gdb.error:
        return []

    result = []
    for line in out.splitlines():
        m = _SECTION_RE.match(line)
        if not m:
            continue
        start = int(m.group(1), 16)
        end = int(m.group(2), 16)
        flags = m.group(4).split()
        if "ALLOC" not in flags or end <= start:
            continue

        perm = Permission.READ
        if "CODE" in flags:
            perm |= Permission.EXECUTE
        if "READONLY" not in flags and "CODE" not in flags:
            perm |= Permission.WRITE

        result.append(Section(page_start=start, page_end=end,
                              permission=perm, path=""))
    return result


def _symbol_address(*names):
    for name in names:
        try:
            return int(gdb.parse_and_eval(f"&{name}"))
        except gdb.error:
            continue
    return None


def _merge_sections(sections):
    """Merge overlapping/adjacent sections with identical permissions."""
    merged = []
    for section in sorted(sections, key=lambda s: (s.page_start, s.page_end)):
        if (merged
                and section.page_start <= merged[-1].page_end
                and section.permission == merged[-1].permission):
            merged[-1].page_end = max(merged[-1].page_end, section.page_end)
        else:
            merged.append(section)
    return merged


def _riscv_maps():
    sections = _elf_sections()

    # Cover the whole SRAM window (GEF otherwise stops at .bss, which is
    # empty in this project because the stack is only a linker symbol).
    stack_top = _symbol_address("_stack_top", "__stack_top", "__stack_top__",
                                "_estack", "__stack")
    ram_end = max(RAM_END, stack_top or 0) + 4
    sections.append(Section(page_start=RAM_START, page_end=ram_end,
                            permission=Permission.READ | Permission.WRITE,
                            path=""))

    # Full flash range: lets `registers`/`dereference` classify code pointers
    # that are outside a named section.
    sections.append(Section(page_start=FLASH_START, page_end=FLASH_END,
                            permission=Permission.READ | Permission.EXECUTE,
                            path=""))

    for start, end in PERIPH_RANGES:
        sections.append(Section(page_start=start, page_end=end,
                                permission=Permission.READ | Permission.WRITE,
                                path=""))

    return iter(_merge_sections(sections))


if getattr(gef.arch, "arch", "") == "RISCV":
    gef.arch.maps = _riscv_maps
    gef.memory.reset_caches()

    # minichlink exposes only the 16 RV32E registers. If a target is already
    # attached, keep exactly the ones that exist; otherwise assume RV32EC.
    try:
        if _main.is_alive():
            present = []
            for reg in gef.arch.all_registers:
                try:
                    value = gdb.parse_and_eval(reg)
                except Exception:
                    continue
                if value.type.code != gdb.TYPE_CODE_VOID:
                    present.append(reg)
            if present:
                gef.arch.all_registers = tuple(present)
        else:
            gef.arch.all_registers = RV32E_REGS
    except Exception:
        pass

    # GEF's RISCV layer advertises a fixed 4-byte instruction length. RV32EC
    # has the C extension, so the "previous N instructions" window skips any
    # 2-byte instruction next to the PC (e.g. the c.li at 0x7e in this build),
    # which makes the code pane look shifted. Zero selects GEF's
    # variable-length backward walk.
    try:
        type(gef.arch).instruction_length = 0
    except Exception:
        pass

    # GEF's backward walk tries `pc - 15*n ... pc - 1` without a lower bound.
    # Near the start of flash that reaches negative addresses, and GDB raises
    # "can't convert negative int to unsigned" (a gdb.error, not MemoryError),
    # which aborts the whole `context` command. Clamp the search at 0.
    def _safe_previous_instruction_address(addr, n):
        if addr < 0 or n <= 0:
            return None
        if gef.arch.instruction_length:
            return max(0, addr - n * gef.arch.instruction_length)

        try:
            cur_insn_addr = _main.gef_current_instruction(addr).address
        except (gdb.error, OverflowError):
            return None

        for i in range(15 * n, 0, -1):
            start_addr = addr - i
            if start_addr < 0:
                continue
            try:
                insns = list(_main.gdb_disassemble(start_addr, end_pc=cur_insn_addr))
            except (gdb.error, OverflowError):
                # The far end of the window can start before flash; try a
                # closer start address instead of giving up on the pane.
                continue

            if len(insns) < n + 1:
                continue

            insns = insns[-n - 1:]
            if insns[-1].address != cur_insn_addr:
                continue
            if all(insn.is_valid() for insn in insns):
                return insns[0].address

        return None

    try:
        _main.gdb_get_nth_previous_instruction_address = _safe_previous_instruction_address
    except Exception:
        pass

    # GEF 2024.06 knows eq/ne/lt/le/ge but not `gt`, so `context` aborts on
    # `bgt`/`bgtu`/`bgtz`, which the delay loops in this firmware use. Also
    # normalise the `c.` prefix used by compressed branch mnemonics.
    def _riscv_is_conditional_branch(self, insn):
        mnemo = insn.mnemonic
        if mnemo.startswith("c."):
            mnemo = mnemo[2:]
        return mnemo.startswith("b")

    def _riscv_is_branch_taken(self, insn):
        mnemo = insn.mnemonic
        if mnemo.startswith("c."):
            mnemo = mnemo[2:]
        condition = mnemo[1:]

        def to_signed(value):
            bits = gef.arch.ptrsize * 8
            if bits == 32 and value & 0x80000000:
                return value - 0x100000000
            if bits == 64 and value & 0x8000000000000000:
                return value - 0x10000000000000000
            return value

        if condition.endswith("z"):
            rs1 = self.register(insn.operands[0])
            rs2 = self.register("$zero")
            condition = condition[:-1]
        elif len(insn.operands) > 2:
            rs1 = self.register(insn.operands[0])
            rs2 = self.register(insn.operands[1])
        else:
            raise OSError(f"RISC-V: Failed to get rs1 and rs2 for instruction: `{insn}`")

        if condition.endswith("u"):
            condition = condition[:-1]
        else:
            rs1 = to_signed(rs1)
            rs2 = to_signed(rs2)

        if condition == "eq":
            taken = rs1 == rs2
            reason = f"{rs1}={rs2}" if taken else f"{rs1}!={rs2}"
        elif condition == "ne":
            taken = rs1 != rs2
            reason = f"{rs1}!={rs2}" if taken else f"{rs1}={rs2}"
        elif condition == "lt":
            taken = rs1 < rs2
            reason = f"{rs1}<{rs2}" if taken else f"{rs1}>={rs2}"
        elif condition == "le":
            taken = rs1 <= rs2
            reason = f"{rs1}<={rs2}" if taken else f"{rs1}>{rs2}"
        elif condition == "gt":
            taken = rs1 > rs2
            reason = f"{rs1}>{rs2}" if taken else f"{rs1}<={rs2}"
        elif condition == "ge":
            taken = rs1 >= rs2
            reason = f"{rs1}>={rs2}" if taken else f"{rs1}<{rs2}"
        else:
            raise OSError(f"RISC-V: Conditional instruction `{insn}` not supported yet")

        return taken, reason

    try:
        type(gef.arch).is_conditional_branch = _riscv_is_conditional_branch
        type(gef.arch).is_branch_taken = _riscv_is_branch_taken
    except Exception:
        pass

    # libopcodes can produce a valid previous instruction at address 0. GEF's
    # `if start_addr:` treats that as "no result", so a PC at 0x4 loses the
    # reset-vector instruction. Reimplement the generator with a None check.
    def _safe_gef_disassemble(addr, nb_insn, nb_prev=0):
        nb_insn = max(1, nb_insn)

        if nb_prev:
            try:
                start_addr = _main.gdb_get_nth_previous_instruction_address(addr, nb_prev)
                if start_addr is not None:
                    for insn in _main.gdb_disassemble(start_addr, count=nb_prev):
                        if insn.address == addr:
                            break
                        yield insn
            except (gdb.error, OverflowError):
                pass

        for insn in _main.gdb_disassemble(addr, count=nb_insn):
            yield insn

    try:
        _main.gef_disassemble = _safe_gef_disassemble
    except Exception:
        pass

    # RV32E has a0-a5 only; GEF's RISC-V class assumes the full a0-a7 ABI.
    try:
        type(gef.arch).function_parameters = ("$a0", "$a1", "$a2",
                                              "$a3", "$a4", "$a5")
    except Exception:
        pass


# --- registers: hex plus decimal -----------------------------------------

@_main.only_if_gdb_running
@_main.parse_arguments({"registers": [""]}, {})
def _registers_do_invoke(self, _argv, **kwargs):
    """GEF's `registers`, with the unsigned decimal value added after the hex."""
    unchanged_color = gef.config["theme.registers_register_name"]
    changed_color = gef.config["theme.registers_value_changed"]
    string_color = gef.config["theme.dereference_string"]
    regs = gef.arch.all_registers

    args = kwargs["arguments"]
    if args.registers and args.registers[0]:
        all_regs = set(gef.arch.all_registers)
        regs = [reg for reg in args.registers if reg in all_regs]
        invalid_regs = [reg for reg in args.registers if reg not in all_regs]
        if invalid_regs:
            err(f"invalid registers for architecture: {', '.join(invalid_regs)}")

    memsize = gef.arch.ptrsize
    endian = str(gef.arch.endianness)
    charset = string.printable
    widest = max(map(len, gef.arch.all_registers))
    special_line = ""

    for regname in regs:
        try:
            reg = gdb.parse_and_eval(regname)
        except Exception:
            continue
        if reg.type.code == gdb.TYPE_CODE_VOID:
            continue

        padreg = regname.ljust(widest, " ")

        if str(reg) == "<unavailable>":
            gef_print(f"{Color.colorify(padreg, unchanged_color)}: "
                      f"{Color.colorify('no value', 'yellow underline')}")
            continue

        value = align_address(int(reg))
        ctx_cmd = gef.gdb.commands["context"]
        assert isinstance(ctx_cmd, ContextCommand)
        old_value = ctx_cmd.old_registers.get(regname, 0)
        color = unchanged_color if value == old_value else changed_color

        if regname in gef.arch.special_registers:
            special_line += f"{Color.colorify(regname, color)}: "
            special_line += f"{gef.arch.register(regname):#04x} "
            continue

        line = f"{Color.colorify(padreg, color)}: "

        if regname == gef.arch.flag_register:
            line += gef.arch.flag_register_to_human()
            gef_print(line)
            continue

        addr = lookup_address(align_address(int(value)))
        if addr.valid:
            line += str(addr)
        else:
            line += format_address_spaces(value)

        # The decimal form is the unsigned 32-bit register value.
        line += Color.colorify(f" ({value:d})", "gray")

        addrs = dereference_from(value)
        if len(addrs) > 1:
            sep = f" {RIGHT_ARROW} "
            line += sep
            line += sep.join(addrs[1:])

        try:
            fmt = f"{endian}{'I' if memsize == 4 else 'Q'}"
            last_addr = int(addrs[-1], 16)
            val = gef_pystring(struct.pack(fmt, last_addr))
            if all(c in charset for c in val):
                line += f' ("{Color.colorify(val, string_color)}"?)'
        except ValueError:
            pass

        gef_print(line)

    if special_line:
        gef_print(special_line)


DetailRegistersCommand.do_invoke = _registers_do_invoke


# --- source pane highlighting -------------------------------------------

_source_cache = {}


def _highlight_source(path, lines):
    if not Pygments or gef.config["gef.disable_color"] is True:
        return lines

    try:
        st = os.stat(path)
    except OSError:
        return lines

    key = (path, st.st_mtime_ns, st.st_size)
    cached = _source_cache.get(key)
    if cached is not None:
        return cached

    try:
        lexer = CLexer() if path.endswith((".c", ".h", ".cc", ".cpp", ".hpp")) else GasLexer()
        text = "\n".join(lines)
        coloured = _pygments_highlight(
            text, lexer, Terminal256Formatter(style="monokai")
        )
        result = coloured.splitlines()
        if len(result) < len(lines):
            result.extend([""] * (len(lines) - len(result)))
        result = result[:len(lines)]
    except Exception:
        result = lines

    _source_cache.clear()
    _source_cache[key] = result
    return result


def context_source(self):
    try:
        pc = gef.arch.pc
        symtabline = gdb.find_pc_line(pc)
        symtab = symtabline.symtab
        # GDB line numbers start at 1.
        line_num = symtabline.line - 1
        if not symtab.is_valid():
            return

        fpath = symtab.fullname()
        with open(fpath, "r") as f:
            lines = [l.rstrip() for l in f.readlines()]
    except Exception:
        return

    try:
        shown = _highlight_source(fpath, lines)
    except Exception:
        shown = lines

    file_base_name = os.path.basename(symtab.filename)
    breakpoints = gdb.breakpoints() or []
    bp_locations = [b.location for b in breakpoints
                    if b.location and file_base_name in b.location]
    past_lines_color = gef.config["theme.old_context"]

    show_full_path_max = self["show_full_source_file_name_max_len"]
    show_basename_path_max = self["show_basename_source_file_name_max_len"]

    nb_line = self["nb_lines_code"]
    fn = symtab.filename
    if len(fn) > show_full_path_max:
        base = os.path.basename(fn)
        if len(base) > show_basename_path_max:
            base = base[-show_basename_path_max:]
        fn = fn[:15] + "[...]" + base
    title = f"source:{fn}+{line_num + 1}"
    cur_line_color = gef.config["theme.source_current_line"]
    self.context_title(title)
    show_extra_info = self["show_source_code_variable_values"]

    for i in range(line_num - nb_line + 1, line_num + nb_line):
        if i < 0 or i >= len(shown):
            continue

        bp_prefix = (Color.redify(BP_GLYPH)
                     if self.line_has_breakpoint(file_base_name, i + 1, bp_locations)
                     else " ")

        if i < line_num:
            gef_print(
                bp_prefix
                + Color.colorify(f"  {i + 1:4d}\t ", past_lines_color)
                + shown[i]
            )

        if i == line_num:
            prefix = f"{bp_prefix}{RIGHT_ARROW[1:]}{i + 1:4d}\t "
            leading = len(lines[i]) - len(lines[i].lstrip())
            if show_extra_info:
                extra_info = self.get_pc_context_info(pc, lines[i])
                if extra_info:
                    gef_print(f"{' ' * (len(prefix) + leading)}{extra_info}")
            gef_print(Color.colorify(prefix, cur_line_color) + shown[i])

        if i > line_num:
            try:
                gef_print(f"{bp_prefix}  {i + 1:4d}\t {shown[i]}")
            except IndexError:
                break
    return


context_source.__doc__ = ContextCommand.context_source.__doc__
ContextCommand.context_source = context_source

# The already-instantiated context command captured the old bound method in
# its layout table, so refresh that entry as well.
try:
    _context = gef.gdb.commands.get("context")
    if _context is not None:
        _context.layout_mapping["source"] = (_context.context_source, None, None)
        # __init__ captured the original generator, not a name lookup.
        _context.instruction_iterator = _main.gef_disassemble
except Exception:
    pass


# --- embedded context profile --------------------------------------------

def _apply_embedded_profile():
    """Apply the bare-metal context defaults without hiding the settings."""
    for key, value in EMBEDDED_PROFILE.items():
        try:
            gef.config[key] = value
        except Exception as exc:
            err(f"gef_riscv: cannot set {key}: {exc}")


_apply_embedded_profile()
