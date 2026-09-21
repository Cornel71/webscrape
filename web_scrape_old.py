import json
import requests
from bs4 import BeautifulSoup
import re
import time
import os
import yaml

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/128.0 Safari/537.36'
}

INPUT_FILE  = "saved_links.txt"
OUTPUT_FILE = "ansible_python_dataset.json"
_HTML_TAG_RE    = re.compile(r'<[a-zA-Z][^>]{0,200}>')
_DOCTYPE_RE     = re.compile(r'<!DOCTYPE\s', re.IGNORECASE)
_HTML_OPEN_RE   = re.compile(r'<html[\s>]', re.IGNORECASE)

def is_html_content(text: str) -> bool:
    """
    Return True if the text looks like raw HTML and should be discarded.

    Checks (any one is enough to reject):
      1. Starts with <!DOCTYPE ...>
      2. Starts with <html ...>
      3. Contains more than 4 HTML-style tags in the first 400 characters
         (catches fragments that don't start at the top of a page)
    """
    stripped = text.lstrip()
    if _DOCTYPE_RE.match(stripped):
        return True
    if _HTML_OPEN_RE.match(stripped):
        return True
    sample = stripped[:400]
    if len(_HTML_TAG_RE.findall(sample)) > 4:
        return True
    return False

_UNICODE_MAP = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
    "\u2014": "--", "\u2015": "--",
    "\u2026": "...",
    "\u00a0": " ",
    "\u00ad": "", "\u200b": "", "\u200c": "", "\u200d": "", "\ufeff": "",
})

def clean_text(text: str) -> str:
    """Normalize smart quotes, dashes, and invisible characters to plain ASCII."""
    return text.translate(_UNICODE_MAP).strip()

def convert_to_raw_github_url(url: str) -> str:
    if 'github.com' in url and '/blob/' in url:
        print("   🔄 Converted blob → raw")
        return url.replace('/blob/', '/raw/')
    return url

_ANSIBLE_DICT_KEYS = {
    'hosts', 'tasks', 'vars', 'roles', 'handlers', 'become',
    'gather_facts', 'name', 'collections', 'pre_tasks', 'post_tasks',
    'vars_files', 'block', 'rescue', 'always',
}

def is_yaml_code(text: str) -> bool:
    """
    Return True if the text parses as YAML and looks like an Ansible
    artifact (playbook, tasks file, handlers file, vars file, etc.),
    based on structure/keys rather than one fixed key ordering.
    """
    stripped = text.strip()
    if not stripped:
        return False
    try:
        loaded = yaml.safe_load(stripped)
    except yaml.YAMLError:
        return False

    if isinstance(loaded, dict):
        return bool(set(loaded.keys()) & _ANSIBLE_DICT_KEYS)

    if isinstance(loaded, list):
        for item in loaded:
            if not isinstance(item, dict):
                continue
            keys = set(item.keys())
            if keys & _ANSIBLE_DICT_KEYS:
                return True
            if any(isinstance(k, str) and (k.islower() or '.' in k) for k in keys):
                return True
        return False

    return False

_PYTHON_PATTERNS = [
    re.compile(r'^\s*import\s+\w+', re.MULTILINE),
    re.compile(r'^\s*from\s+[\w.]+\s+import\s+', re.MULTILINE),
    re.compile(r'^\s*def\s+\w+\s*\(', re.MULTILINE),
    re.compile(r'^\s*class\s+\w+', re.MULTILINE),
    re.compile(r'^\s*if\s+__name__\s*==', re.MULTILINE),
    re.compile(r'\bprint\('),
    re.compile(r'^\s*return\s+', re.MULTILINE),
]

def is_python_code(text: str) -> bool:
    """
    Return True only if the text has real Python syntax. Deliberately
    does NOT match on the bare substring 'ansible.' or 'module_utils',
    since those also appear inside YAML FQCN module names.
    """
    return any(p.search(text) for p in _PYTHON_PATTERNS)

def clean_content(text: str) -> str:
    """Strip control characters, trailing whitespace, and normalize unicode."""
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]', '', text)
    lines = [line.rstrip() for line in text.split('\n') if line.strip()]
    return clean_text('\n'.join(lines))

def scrape_url(url: str) -> dict | None:
    try:
        original_url = url.strip()
        url = convert_to_raw_github_url(original_url)
        print(f"🌐 Scraping: {original_url}")
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        raw_text = resp.text.strip()
        if 'github.com' in original_url or url.endswith(('.yml', '.yaml', '.py')):
            filename = original_url.split('/')[-1].split('?')[0]
            if is_html_content(raw_text):
                print(f"   ⚠️  Skipped: raw content looks like HTML → {filename}")
                return None
            if is_yaml_code(raw_text):
                prompt = f"Ansible Playbook for: {filename}"
                lang   = "YAML"
            elif is_python_code(raw_text):
                prompt = f"Python script for: {filename}"
                lang   = "Python"
            else:
                print(f"   ⚠️  Skipped: no YAML/Python code detected → {filename}")
                return None
            print(f"   ✅ SUCCESS ({lang}) → {filename}")
            return {"conversations": [
                {"from": "human", "value": clean_text(prompt)},
                {"from": "gpt",   "value": clean_content(raw_text)},
            ]}
        soup = BeautifulSoup(resp.text, 'html.parser')
        title_tag  = soup.find('title')
        page_title = title_tag.get_text(strip=True) if title_tag else "Ansible Guide"
        page_title = clean_text(re.sub(r'\s+', ' ', page_title)[:160])
        code_blocks = []
        for pre in soup.find_all('pre'):
            code_tag = pre.find('code') or pre
            block = code_tag.get_text().strip()
            if (len(block) > 100
                    and not is_html_content(block)
                    and (is_yaml_code(block) or is_python_code(block))):
                code_blocks.append(block)
        selectors = [
            'div.highlight', 'pre.highlight', 'div.code',
            'div.language-yaml', 'div.language-python',
        ]
        for selector in selectors:
            for el in soup.select(selector):
                block = el.get_text().strip()
                if (len(block) > 120
                        and not is_html_content(block)
                        and (is_yaml_code(block) or is_python_code(block))):
                    code_blocks.append(block)
        if not code_blocks:
            print(f"   ⚠️  Skipped: no YAML or Python code found → {page_title}")
            return None
        best_code = clean_content(max(code_blocks, key=len))
        if is_html_content(best_code):
            print(f"   ⚠️  Skipped: best block is HTML → {page_title}")
            return None
        prompt = (f"Ansible Playbook for: {page_title}"
                  if is_yaml_code(best_code)
                  else f"Python script for: {page_title}")
        print("   ✅ SUCCESS → YAML/Python code extracted from page")
        return {"conversations": [
            {"from": "human", "value": clean_text(prompt)},
            {"from": "gpt",   "value": best_code},
        ]}

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None

if __name__ == "__main__":
    if not os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, 'w', encoding='utf-8') as f:
            f.write("# One URL per line. Lines starting with # are ignored.\n")
        print(f"Created {INPUT_FILE} — add your URLs and re-run.")
    else:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            urls = [l.strip() for l in f if l.strip() and not l.strip().startswith('#')]
        print(f"📋 Loaded {len(urls)} URLs\n")
        dataset = []
        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}]")
            result = scrape_url(url)
            if result:
                dataset.append(result)
            time.sleep(1.5)
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, indent=2, ensure_ascii=False)
        print(f"\n🎉 Done! Saved {len(dataset)} conversations → {OUTPUT_FILE}")
