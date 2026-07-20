#!/usr/bin/env python3
"""Full inventory of 'All recon data' — branch / channel / month / filetypes."""
from pathlib import Path
from collections import defaultdict

ROOT = Path("All recon data")

# branch -> channel -> month -> {ext: [filenames]}
tree = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))

def walk(base: Path, branch, channel, month):
    for p in base.iterdir():
        if p.is_dir():
            # descend; classify by depth
            if branch is None:
                walk(p, p.name, None, None)
            elif channel is None:
                walk(p, branch, p.name, None)
            elif month is None:
                walk(p, branch, channel, p.name)
            else:
                # deeper nesting — fold into current month bucket
                walk(p, branch, channel, month)
        else:
            if branch and channel and month:
                ext = p.suffix.lower() or "(noext)"
                tree[branch][channel][month][ext].append(p.name)
            elif branch and channel:
                tree[branch][channel]["(loose)"][p.suffix.lower() or "(noext)"].append(p.name)

walk(ROOT, None, None, None)

out = []
for branch in sorted(tree):
    out.append(f"\n{'='*72}\nBRANCH: {branch}\n{'='*72}")
    for channel in sorted(tree[branch]):
        out.append(f"\n  CHANNEL: {channel}")
        for month in sorted(tree[branch][channel]):
            exts = tree[branch][channel][month]
            total = sum(len(v) for v in exts.values())
            ext_summary = ", ".join(f"{e}:{len(v)}" for e, v in sorted(exts.items()))
            out.append(f"    [{month}] {total} files  ({ext_summary})")
            # show a couple of sample filenames per ext to identify document type
            for e in sorted(exts):
                for s in exts[e][:3]:
                    out.append(f"         {e}: {s}")

text = "\n".join(out)
Path("inventory_recon.txt").write_text(text, encoding="utf-8")
print("Wrote inventory_recon.txt")
print(text.encode("ascii", "replace").decode("ascii"))
