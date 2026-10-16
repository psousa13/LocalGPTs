import importlib.util, pathlib, py_compile, re, shutil, sys, textwrap

base = pathlib.Path(importlib.util.find_spec("open_webui").origin).parent

def backup(f):
    b = f.with_name(f.name + ".bak")
    if b.exists():
        return b
    shutil.copy(f, b)
    return b

# 1) strict template: no "use your own knowledge" clause
cfg = base / "config.py"
t = cfg.read_text(encoding="utf-8")
old = ("- If the answer isn't present in the context but you possess the knowledge, "
       "explain this to the user and provide the answer using your own understanding.")
new = ("- Use ONLY the context. If the answer is not in the context, reply exactly: "
       "I could not find this in the provided documents. Never use outside knowledge.")
if old in t:
    backup(cfg)
    cfg.write_text(t.replace(old, new), encoding="utf-8")
    print("TEMPLATE PATCHED")
elif new in t:
    print("TEMPLATE ALREADY PATCHED")
else:
    print("TEMPLATE LINE NOT FOUND"); sys.exit(1)

# 2) score-based gate (always rebuilt from the original file)
f = base / "utils" / "middleware.py"
bak = backup(f)
shutil.copy(bak, f)
t = f.read_text(encoding="utf-8")
m = list(re.finditer(r"^( *)if sources and prompt:\n.*\n", t, flags=re.M))
if len(m) != 1:
    print("ANCHOR NOT FOUND:", len(m)); sys.exit(1)
ind = m[0].group(1)
gate = textwrap.indent(textwrap.dedent('''\
    # RAGONLY-GATE: documents expected but nothing relevant retrieved -> model never sees the question
    import os as _os
    _has_docs = bool(model_knowledge) or bool(metadata.get('files')) or bool(form_data.get('files'))
    if _has_docs:
        _ndocs, _scores = 0, []
        for _s in (sources or []):
            if not isinstance(_s, dict):
                continue
            _d = _s.get('distances') or []
            _md = _s.get('metadata') or []
            for _i, _doc in enumerate(_s.get('document') or []):
                _ndocs += 1
                _v = _d[_i] if _i < len(_d) else None
                if _v is None and _i < len(_md) and isinstance(_md[_i], dict):
                    _v = _md[_i].get('score')
                if isinstance(_v, (int, float)):
                    _scores.append(float(_v))
        _min = float(_os.getenv('RAGONLY_MIN_SCORE', '0'))
        _hit = (max(_scores) >= _min) if _scores else (_ndocs > 0)
        print('[RAGONLY] docs=%d scores=%s min=%s hit=%s' % (_ndocs, [round(x, 3) for x in _scores], _min, _hit), flush=True)
        if not _hit and prompt:
            for _m in reversed(form_data['messages']):
                if _m.get('role') == 'user':
                    _m['content'] = 'Reply with exactly this sentence and nothing else: I could not find this in the provided documents.'
                    break
    '''), ind)
f.write_text(t[:m[0].end()] + gate + t[m[0].end():], encoding="utf-8")
py_compile.compile(str(f), doraise=True)
print("GATE PATCHED OK:", f)
