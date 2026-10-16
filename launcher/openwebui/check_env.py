import sys, importlib.metadata as m
bad, n = [], 0
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#") or "==" not in line:
        continue
    name, ver = line.split("==", 1)
    ver = ver.split(";")[0].strip().split("+")[0]
    n += 1
    try:
        got = m.version(name).split("+")[0]
    except m.PackageNotFoundError:
        got = None
    if got != ver:
        bad.append("%s==%s (installed: %s)" % (name, ver, got))
for p in ("open-webui", "torch", "transformers", "sentence-transformers", "chromadb"):
    try:
        print("%s %s" % (p, m.version(p)))
    except m.PackageNotFoundError:
        print("%s NOT INSTALLED" % p)
print("pinned packages matching: %d of %d" % (n - len(bad), n))
for b in bad[:15]:
    print("  missing/different: " + b)
sys.exit(1 if bad else 0)
