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

"""Line length."""

import re

from mynewt_style import FileContext, Rule, register_rule
from mynewt_style.wrap import wrap_line, wrap_lines


@register_rule
class LineLength(Rule):
    name = "line-length"
    category = "layout"
    summary = "Lines over 80 columns are warnings, over 100 are errors."
    explanation = """
        Keep lines at or below settings.target_line_length (80) where it reads
        well; going up to settings.max_line_length (100) is fine when breaking
        the line would hurt readability. Longer lines are errors.

        Lines matching one of 'ignore_patterns' (URLs, #include lines by
        default) are exempt because they cannot be broken sensibly.

        The fix wraps over-long code lines after the last ',' / '&&' / '||'
        that fits, aligning the rest the way the code base already does:

            rc = ble_gattc_write_flat(conn_handle, attr_handle, value, len,
                                      ble_gattc_write_cb, NULL);

        Comments, string literals and macros are never rewrapped; if no safe
        break point exists the line is left for you to shorten.

        Lines between target and max are warnings: shown, but they only fail
        the run with -W. Set 'report_target = false' to hide them.
    """
    fixable = True
    options = {
        "report_target": True,
        "ignore_patterns": [r"https?://", r"^\s*#\s*include\b"],
    }

    def _ignored(self, line: str) -> bool:
        return any(re.search(p, line) for p in self.opts["ignore_patterns"])

    def check(self, ctx: FileContext):
        hard = int(ctx.settings["max_line_length"])
        soft = int(ctx.settings["target_line_length"])
        out = []
        for ln, raw in enumerate(ctx.lines, 1):
            line = raw.rstrip("\r")
            n = len(line)
            if n <= soft or self._ignored(line):
                continue
            if n > hard:
                fixable = wrap_line(ctx, ln, hard) is not None
                out.append(self.violation(ctx, ln, hard + 1,
                                          f"line is {n} characters (max {hard})", fixable))
            elif self.opts["report_target"]:
                v = self.violation(ctx, ln, soft + 1,
                                   f"line is {n} characters (target {soft}, max {hard})", False)
                v.severity = "warning"
                out.append(v)
        return out

    def fix(self, ctx: FileContext):
        hard = int(ctx.settings["max_line_length"])
        targets = [ln for ln, raw in enumerate(ctx.lines, 1)
                   if len(raw.rstrip("\r")) > hard and not self._ignored(raw)]
        if not targets:
            return None
        new = wrap_lines(ctx.content, ctx.path, ctx.settings, only=targets)
        return new if new != ctx.content else None
