#
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
#

"""Preprocessor directives and macros."""

import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional

from mynewt_style import FileContext, Rule, register_rule
from mynewt_style.cparse import split_top_level

_TAKES_ARG = {"define", "undef", "include", "if", "ifdef", "ifndef", "elif",
              "error", "warning", "pragma", "line", "include_next"}

_DIRECTIVE_RE = re.compile(r"^([ \t]*)#([ \t]*)(\w+)([ \t]*)(.*)$")


def _strip_eol(line: str):
    return (line[:-1], "\r") if line.endswith("\r") else (line, "")


@register_rule
class DirectiveFormat(Rule):
    name = "directive-format"
    category = "preprocessor"
    summary = "'#' in column 0, no space after '#', one space before the argument."
    explanation = """
        Preprocessor directives start in column 0 and are not indented, even
        inside functions or nested #if blocks:

            Good:  #if MYNEWT_VAL(BLE_ROLE_CENTRAL)      #include "host/ble_hs.h"
            Bad:       #if MYNEWT_VAL(BLE_ROLE_CENTRAL)  #  include  "host/ble_hs.h"

        The spacing *after* a '#define NAME' (the value column) is handled by
        'define-alignment' and is not touched here.
    """
    fixable = True
    options = {"allow_space_after_hash": False}

    def _problems(self, ctx: FileContext):
        for d in ctx.directives:
            raw, eol = _strip_eol(ctx.lines[d.start_line - 1])
            m = _DIRECTIVE_RE.match(raw)
            if not m:
                continue
            lead, after_hash, name, gap, arg = m.groups()
            fixed = "#"
            msgs = []
            if lead:
                msgs.append((1, "preprocessor directive must start in column 1"))
            if after_hash and not self.opts["allow_space_after_hash"]:
                msgs.append((len(lead) + 2, "no space allowed between '#' and the directive"))
                fixed += name
            else:
                fixed += after_hash + name
            if name in _TAKES_ARG and arg:
                # '#if(x)' is unusual but harmless; '#include<x>' is not.
                want = "" if gap == "" and name != "include" else " "
                if gap != want:
                    col = len(lead) + 1 + len(after_hash) + len(name) + 1
                    msgs.append((col, f"use exactly one space after '#{name}'"))
                fixed += want + arg
            else:
                # '#endif  /* FOO */' etc: spacing before a comment is free.
                fixed += gap + arg
            if msgs:
                yield d.start_line, msgs, fixed + eol

    def check(self, ctx: FileContext):
        return [self.violation(ctx, ln, col, msg)
                for ln, msgs, _ in self._problems(ctx) for col, msg in msgs]

    def fix(self, ctx: FileContext):
        lines = ctx.content.split("\n")
        changed = False
        for ln, _, fixed in self._problems(ctx):
            if lines[ln - 1] != fixed:
                lines[ln - 1] = fixed
                changed = True
        return "\n".join(lines) if changed else None


_DEFINE_RE = re.compile(r"^#define[ \t]+(\w+(?:\([^)]*\))?)([ \t]+)(\S.*)$")


@dataclass
class _Def:
    line: int          # 1-based
    name_end: int      # visual column just after NAME / NAME(args)
    gap: int
    value_col: int     # visual column of the value
    has_tab: bool
    multiline: bool    # value continues on following lines


@register_rule
class DefineAlignment(Rule):
    name = "define-alignment"
    category = "preprocessor"
    summary = "#define values: one space, or aligned in a column with neighbouring #defines."
    explanation = """
        A #define is separated from its value by a single space, *unless* the
        author lined the values up in a column. Lined-up groups are tidy and
        are left exactly as they are:

            #define BLE_ATT_OP_ERROR_RSP        0x01     <- fine (aligned group)
            #define BLE_ATT_OP_MTU_REQ          0x02
            #define BLE_ATT_OP_MTU_RSP          0x03

            #define BLE_FOO 1                            <- fine (single space)

            #define BLE_BAR      1                       <- violation: extra spaces
                                                            that align with nothing

        A value counts as aligned if at least one other #define in the file
        has its value in the same column - so a file-wide column (e.g. all
        HCI opcodes at column 57, each above its own struct) is respected.

        The fix moves a stray value into the column used by its neighbours
        (#defines separated only by blank or comment lines) when at least two
        of them share it. Padded neighbours that just miss each other are
        lined up on the widest of their columns. Anything else gets a single
        space.
    """
    fixable = True

    def _groups(self, ctx: FileContext) -> List[List[_Def]]:
        tw = int(ctx.settings["tab_width"])
        groups: List[List[_Def]] = []
        cur: List[_Def] = []
        last_end = None
        blank_or_comment = set()
        for ln, (raw, code) in enumerate(zip(ctx.lines, ctx.code_lines), 1):
            if not code.strip() and (not raw.strip() or ln in ctx.comment_lines):
                blank_or_comment.add(ln)
        for d in ctx.directives:
            if d.name != "define":
                continue
            raw, _ = _strip_eol(ctx.lines[d.start_line - 1])
            m = _DEFINE_RE.match(raw)
            if not m or m.group(3).strip() == "\\":
                continue
            # Visual columns, so tab-aligned values compare correctly.
            name_end = len(raw[:m.end(1)].expandtabs(tw))
            value_col = len(raw[:m.start(3)].expandtabs(tw))
            entry = _Def(d.start_line, name_end, value_col - name_end, value_col, "\t" in raw,
                         d.end_line > d.start_line)
            contiguous = last_end is not None and all(
                ln in blank_or_comment for ln in range(last_end + 1, d.start_line))
            if not contiguous and cur:
                groups.append(cur)
                cur = []
            cur.append(entry)
            last_end = d.end_line
        if cur:
            groups.append(cur)
        return groups

    @staticmethod
    def _stray(groups: List[List[_Def]]):
        """Defines with extra spaces whose value column no other define uses."""
        cols = Counter(d.value_col for g in groups for d in g)
        # Multi-line defines often have continuation lines aligned under the
        # value; they serve as alignment anchors but are never moved.
        return [d for g in groups for d in g
                if d.gap > 1 and cols[d.value_col] < 2 and not d.multiline]

    def check(self, ctx: FileContext):
        return [self.violation(ctx, d.line, d.name_end + 1,
                               f"{d.gap} spaces before the #define value align with no "
                               f"other #define; use one space or line it up with its "
                               f"neighbours")
                for d in self._stray(self._groups(ctx))]

    @staticmethod
    def _fix_column(d: _Def, group: List[_Def], stray: set) -> int:
        """Where the value of stray define d should go."""
        others = Counter(o.value_col for o in group
                         if o is not d and o.gap > 1 and o.value_col > d.name_end)
        # A column shared by at least two neighbours is real alignment.
        shared = [c for c, n in others.most_common() if n >= 2]
        if shared:
            return shared[0]
        # Several padded defines that just miss each other: the author meant
        # to align them, so line them up on the widest of their columns.
        near = [o.value_col for o in group
                if o is not d and id(o) in stray and o.value_col > d.name_end]
        if near:
            return max(near + [d.value_col])
        return d.name_end + 1

    def fix(self, ctx: FileContext):
        lines = ctx.content.split("\n")
        changed = False
        groups = self._groups(ctx)
        stray = {id(d) for d in self._stray(groups)}
        for group in groups:
            for d in group:
                if id(d) not in stray or d.has_tab:
                    continue  # tabs: let no-tabs normalise the line first
                target = self._fix_column(d, group, stray)
                raw = lines[d.line - 1]
                lines[d.line - 1] = raw[:d.name_end] + " " * (target - d.name_end) + \
                    raw[d.value_col:]
                d.gap, d.value_col = target - d.name_end, target
                changed = True
        return "\n".join(lines) if changed else None


@register_rule
class MacroContinuation(Rule):
    name = "macro-continuation"
    category = "preprocessor"
    summary = "Line-continuation backslashes of a macro are aligned (or all one space away)."
    explanation = """
        In a multi-line macro every trailing '\\' sits in the same column, so
        the macro reads as one block:

            #define BLE_FOO(x)                \\
                do {                          \\
                    ble_foo_do(x);            \\
                } while (0)

        Using exactly one space before every '\\' is accepted too. A mix of
        both, or a '\\' glued to the code, is reported. The fix aligns all
        backslashes to the most used existing column that fits every line, or
        just past the longest line.
    """
    fixable = True

    def _macros(self, ctx: FileContext):
        tw = int(ctx.settings["tab_width"])
        for d in ctx.directives:
            if d.end_line == d.start_line:
                continue
            rows = []
            for ln in range(d.start_line, d.end_line):
                raw, _ = _strip_eol(ctx.lines[ln - 1])
                if not raw.endswith("\\"):
                    break
                content = raw[:-1].rstrip(" \t").expandtabs(tw)
                rows.append((ln, content, len(raw[:-1].expandtabs(tw))))
            if rows:
                yield d, rows

    @staticmethod
    def _target(rows) -> int:
        """Most used existing column that fits every line, else just past the longest."""
        longest = max(len(content) for _, content, _ in rows)
        fitting = Counter(col for _, _, col in rows if col > longest)
        return fitting.most_common(1)[0][0] if fitting else longest + 1

    @staticmethod
    def _ok(rows) -> bool:
        cols = {col for _, _, col in rows}
        gaps = {col - len(content) for _, content, col in rows}
        if 0 in gaps:
            return False
        return len(cols) == 1 or gaps == {1}

    def check(self, ctx: FileContext):
        out = []
        for d, rows in self._macros(ctx):
            if self._ok(rows):
                continue
            target = self._target(rows)
            bad = next(r for r in rows if r[2] != target)
            out.append(self.violation(ctx, bad[0], bad[2] + 1,
                                      "line-continuation '\\' not aligned with the rest "
                                      "of the macro"))
        return out

    def fix(self, ctx: FileContext):
        lines = ctx.content.split("\n")
        changed = False
        for d, rows in self._macros(ctx):
            if self._ok(rows):
                continue
            if any("\t" in ctx.lines[ln - 1] for ln, _, _ in rows):
                continue  # let no-tabs normalise the macro first
            target = self._target(rows)
            for ln, content, _ in rows:
                _, eol = _strip_eol(lines[ln - 1])
                lines[ln - 1] = content.ljust(target) + "\\" + eol
            changed = True
        return "\n".join(lines) if changed else None


@register_rule
class MacroMultiStatement(Rule):
    name = "macro-multi-statement"
    category = "preprocessor"
    summary = "Function-like macros with several statements are wrapped in do { } while (0)."
    explanation = """
        A macro that expands to several statements breaks when used as the
        body of an unbraced if/else. Wrap it so it behaves like one statement:

            Good:  #define RESET(x)  do { (x)->a = 0; (x)->b = 0; } while (0)
            Bad:   #define RESET(x)  (x)->a = 0; (x)->b = 0

        Macros that expand to declarations or definitions (starting with a
        type, 'static', 'struct', ...) are not checked. Not auto-fixable.
    """
    severity = "warning"

    def check(self, ctx: FileContext):
        out = []
        for d in ctx.directives:
            if d.name != "define":
                continue
            text = "\n".join(ctx.code_lines[d.start_line - 1:d.end_line])
            m = re.match(r"\s*#\s*define\s+\w+\(([^)]*)\)", text)
            if not m:
                continue
            body = text[m.end():].replace("\\\n", " ").strip()
            if not body or re.match(r"(do\b|\(\{|static\b|struct\b|union\b|enum\b|"
                                    r"const\b|extern\b|typedef\b|void\b|int\b|\w+_t\b)", body):
                continue
            stmts = [s for s in split_top_level(body, sep=";") if s.strip()]
            if len(stmts) > 1:
                out.append(self.violation(ctx, d.start_line, 1,
                                          "multi-statement macro should be wrapped in "
                                          "'do { ... } while (0)'"))
        return out


_SAFE_BEFORE = {None, "(", ",", "[", "{", ";", "=", "return"}
_SAFE_AFTER = {None, ")", ",", "]", "}", ";"}
_GLUE = {"#", "##", ".", "->", "struct", "union", "enum"}


@register_rule
class MacroArgParens(Rule):
    name = "macro-arg-parens"
    category = "preprocessor"
    summary = "Macro parameters are parenthesised in the macro body: (x)."
    explanation = """
        CERT PRE01-C. An argument is substituted as text, so without
        parentheses operator precedence leaks between argument and body:

            #define DOUBLE(x)  x * 2        DOUBLE(a + 1)  ->  a + 1 * 2
            #define DOUBLE(x)  ((x) * 2)    DOUBLE(a + 1)  ->  ((a + 1) * 2)

        A parameter does not need parentheses when it is already delimited
        on both sides (a whole function argument, array index, initializer
        element, right-hand side of '='), when it is stringified/pasted
        ('#x', 'a##x'), used as a member name ('p->x') or a type/declarator
        ('struct x', 'x name'). Reported only: adding parentheses blindly
        would break macros that take types.
    """
    severity = "warning"

    def check(self, ctx: FileContext):
        from mynewt_style.helpers import tokens
        out = []
        for d in ctx.directives:
            if d.name != "define":
                continue
            text = "\n".join(ctx.code_lines[d.start_line - 1:d.end_line])
            text = text.replace("\\\n", "  ")
            m = re.match(r"\s*#\s*define\s+\w+\(([^)]*)\)", text)
            if not m:
                continue
            params = [p.strip() for p in m.group(1).split(",") if p.strip() not in ("", "...")]
            toks = [t.text for t in tokens(text, m.end())]
            reported = set()
            for i, tok in enumerate(toks):
                if tok not in params or tok in reported:
                    continue
                before = toks[i - 1] if i > 0 else None
                after = toks[i + 1] if i + 1 < len(toks) else None
                if before in _GLUE or after == "##":
                    continue
                if before in _SAFE_BEFORE and after in _SAFE_AFTER:
                    continue
                if after is not None and re.match(r"[A-Za-z_]", after):
                    continue  # used as a type: 'x name'
                if before is not None and re.match(r"[A-Za-z_]", before) and before != "return":
                    continue  # used as a declarator: 'type x'
                if before is not None and re.match(r"[A-Za-z_]", before) and before != "return":
                    continue  # used as a declarator: 'type x'
                reported.add(tok)
                out.append(self.violation(ctx, d.start_line, 1,
                                          f"macro parameter '{tok}' is used without "
                                          f"parentheses"))
        return out


@register_rule
class DuplicateInclude(Rule):
    name = "duplicate-include"
    category = "preprocessor"
    summary = "Each header is included once per file."
    explanation = """
        A second '#include' of the same header is dead weight and usually a
        merge leftover. Includes in different #if branches are fine.
    """

    def check(self, ctx: FileContext):
        out = []
        branch: List[int] = []   # stack of branch ids
        next_id = 0
        seen = {}                # header -> branch path where first included
        for d in ctx.directives:
            line = ctx.lines[d.start_line - 1]
            if d.name in ("if", "ifdef", "ifndef"):
                next_id += 1
                branch.append(next_id)
            elif d.name in ("elif", "else") and branch:
                next_id += 1
                branch[-1] = next_id
            elif d.name == "endif" and branch:
                branch.pop()
            elif d.name == "include":
                m = re.search(r"[<\"]([^>\"]+)[>\"]", line)
                if not m:
                    continue
                path = tuple(branch)
                first = seen.get(m.group(1))
                if first is not None and path[:len(first)] == first:
                    out.append(self.violation(ctx, d.start_line, 1,
                                              f"'{m.group(1)}' is already included"))
                elif first is None:
                    seen[m.group(1)] = path
        return out


@register_rule
class NoIfZero(Rule):
    name = "no-if-0"
    category = "preprocessor"
    summary = "No '#if 0' blocks; delete dead code (git remembers it)."
    explanation = """
        Code disabled with '#if 0' rots: it is never compiled, so it stops
        matching the code around it. Remove it, or make it a real option
        ('#if MYNEWT_VAL(...)').
    """
    severity = "warning"

    def check(self, ctx: FileContext):
        return [self.violation(ctx, d.start_line, 1, "'#if 0' block")
                for d in ctx.directives
                if d.name == "if" and re.match(r"\s*#\s*if\s+0\s*($|/[*/])",
                                               ctx.code_lines[d.start_line - 1] + "\n")]
