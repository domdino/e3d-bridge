"""Export report. One report.txt next to the OBJ, every run.

This is how the user debugs, and how we debug.
"""

import time


class Report:
    def __init__(self):
        self.passthrough = []   # (material_name, detail)
        self.baked = []         # (material_name, detail)  - Phase 2
        self.skipped = []       # (name, reason)
        self.warnings = []
        self.objects = []       # (object_name, detail)
        self.textures = []      # (relative_path, detail)
        self.tagged = []        # (export_name, tag) - props/gimbal isolation
        self.notes = []         # human guidance, not problems
        self.errors = []

    def warn(self, msg):
        self.warnings.append(msg)

    def error(self, msg):
        self.errors.append(msg)

    def write(self, filepath, settings_summary=""):
        lines = []
        add = lines.append
        add("E3D Bridge export report")
        add(time.strftime("%Y-%m-%d %H:%M:%S"))
        if settings_summary:
            add(settings_summary)
        add("")

        def section(title, rows, fmt):
            add("%s (%d)" % (title, len(rows)))
            if not rows:
                add("  (none)")
            for row in rows:
                add("  " + fmt(row))
            add("")

        section("Objects exported", self.objects, lambda r: "%s - %s" % r)
        section("Materials passed through", self.passthrough, lambda r: "%s - %s" % r)
        section("Materials baked", self.baked, lambda r: "%s - %s" % r)
        section("Skipped", self.skipped, lambda r: "%s - %s" % r)
        section("Textures written", self.textures, lambda r: "%s - %s" % r)
        section("Tagged parts (props/gimbal)", self.tagged, lambda r: "%s - %s" % r)
        section("Notes", self.notes, lambda r: r)
        section("Warnings", self.warnings, lambda r: r)
        section("Errors", self.errors, lambda r: r)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return filepath
