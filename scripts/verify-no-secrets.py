#!/usr/bin/env python3
"""Fail if likely live credentials appear in the repository."""
from pathlib import Path
import re, sys
ROOT = Path(__file__).resolve().parents[1]
patterns = {
    'github_pat': re.compile(r'github_pat_[A-Za-z0-9_]+'),
    'classic_github_token': re.compile(r'ghp_[A-Za-z0-9]+'),
    'amazon_client_secret': re.compile(r'amzn1\.oa2-cs\.[A-Za-z0-9]+'),
    'openai_style_key': re.compile(r'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}'),
    'bearer_value': re.compile(r'Bearer\s+[A-Za-z0-9._~+/=-]{24,}'),
}
ignored = {'.git', '__pycache__'}
hits=[]
for path in ROOT.rglob('*'):
    if not path.is_file() or any(part in ignored for part in path.parts):
        continue
    try: text=path.read_text(encoding='utf-8')
    except UnicodeDecodeError: continue
    for name,pat in patterns.items():
        if pat.search(text): hits.append((name,str(path.relative_to(ROOT))))
if hits:
    for h in hits: print(f'{h[0]}: {h[1]}')
    raise SystemExit(1)
print('secret-scan: OK')
