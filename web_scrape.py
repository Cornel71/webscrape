import ast
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
#in case github rate limit reached
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
INPUT_FILE  = "saved_links.txt"
OUTPUT_FILE = "ansible_yaml_dataset.json"

CODE_EXTENSIONS = ('.yml', '.yaml', '.py')

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

_PYTHON_ANSIBLE_MARKERS = (
    'AnsibleModule', 'ansible.module_utils', 'ansible_collections',
    'from ansible', 'import ansible', 'DOCUMENTATION =', 'DOCUMENTATION=',
    'EXAMPLES =', 'EXAMPLES=', 'RETURN =', 'RETURN=',
    'ActionModule', 'CallbackBase', 'LookupBase', 'FilterModule',
)

def is_python_code(text: str, require_ansible_context: bool = True) -> bool:
    """
    Return True if the text is syntactically valid Python.

    When require_ansible_context is True (the default — used for files
    discovered via generic HTML scraping), also require at least one
    marker tying the code to Ansible (module boilerplate, ansible.*
    imports, etc.) so we don't hoover up unrelated Python snippets.
    Files pulled directly from a GitHub repo already live inside an
    Ansible-related repo, so that extra check is skipped for those.
    """
    stripped = text.strip()
    if not stripped:
        return False
    try:
        ast.parse(stripped)
    except SyntaxError:
        return False

    if not require_ansible_context:
        return True

    return any(marker in stripped for marker in _PYTHON_ANSIBLE_MARKERS)

def clean_content(text: str) -> str:
    """Strip control characters, trailing whitespace, and normalize unicode."""
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]', '', text)
    lines = [line.rstrip() for line in text.split('\n') if line.strip()]
    return clean_text('\n'.join(lines))

_GITHUB_URL_RE = re.compile(
    r'github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?'
    r'(?:/(?:tree|blob)/(?P<branch>[^/]+)(?:/(?P<path>[^?#]*))?)?'
    r'/?(?:[?#].*)?$'
)

def _github_api_headers() -> dict:
    h = dict(HEADERS)
    h["Accept"] = "application/vnd.github+json"
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h

def parse_github_url(url: str):
    """Parse a github.com URL into (owner, repo, branch_or_None, path_or_None)."""
    m = _GITHUB_URL_RE.search(url.strip())
    if not m or not m.group('owner') or not m.group('repo'):
        return None
    return m.group('owner'), m.group('repo'), m.group('branch'), m.group('path')

def get_default_branch(owner: str, repo: str) -> str:
    resp = requests.get(f"https://api.github.com/repos/{owner}/{repo}",
                         headers=_github_api_headers(), timeout=20)
    resp.raise_for_status()
    return resp.json()["default_branch"]

def list_repo_code_urls(owner: str, repo: str, branch: str | None = None,
                         subpath: str | None = None) -> list[str]:
    """
    Recursively walk the whole repo tree (one call to the GitHub Trees API,
    recursive=1) and return raw.githubusercontent.com URLs for every
    .yml/.yaml/.py file found anywhere in it, optionally restricted to
    files under `subpath` (so a /tree/branch/some/folder URL only pulls
    that folder and its subdirectories).
    """
    if not branch:
        branch = get_default_branch(owner, repo)

    api_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"
    resp = requests.get(api_url, headers=_github_api_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if data.get("truncated"):
        print("   ⚠️  GitHub API truncated the tree (repo is very large); "
              "some deeply nested files may be missing")

    raw_base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
    subpath = subpath.strip('/') if subpath else None

    code_urls = []
    for item in data.get("tree", []):
        if item.get("type") != "blob":
            continue
        path = item["path"]
        if subpath and not (path == subpath or path.startswith(subpath + '/')):
            continue
        if path.endswith(CODE_EXTENSIONS):
            code_urls.append(raw_base + path)
    return code_urls

def expand_github_url(url: str) -> list[str]:
    """
    Given any github.com URL, return the list of raw file URLs to scrape:
      - repo root, or a /tree/branch/subdir URL -> every .yml/.yaml/.py
        file under it, walking all subdirectories recursively
      - a /blob/ URL to a .yml/.yaml/.py file    -> that single raw file
      - a /blob/ URL to anything else             -> [] (skipped)
    """
    parsed = parse_github_url(url)
    if not parsed:
        print(f"   ⚠️  Could not parse as a GitHub URL → {url}")
        return []
    owner, repo, branch, path = parsed

    is_blob = '/blob/' in url
    if is_blob:
        if path and path.endswith(CODE_EXTENSIONS):
            return [convert_to_raw_github_url(url)]
        print(f"   ⚠️  Skipped: linked file isn't YAML/Python → {url}")
        return []

    print(f"   🔍 Traversing GitHub repo {owner}/{repo}"
          f"{' (' + path + ')' if path else ''} for YAML/Python files, including subdirs ...")
    try:
        code_urls = list_repo_code_urls(owner, repo, branch, path)
    except Exception as e:
        print(f"   ❌ Failed to list repo contents: {e}")
        return []
    print(f"   📄 Found {len(code_urls)} file(s)")
    return code_urls

def gather_urls_to_scrape(input_url: str) -> list[str]:
    """
    Resolve one line of saved_links.txt into the URL(s) to actually fetch.
      - GitHub links: expanded to every YAML/Python file found, recursing
        into all subdirectories.
      - Anything else: scraped as-is; scrape_url() extracts YAML/Python only.
    """
    if 'github.com' in input_url:
        return expand_github_url(input_url)
    return [input_url]

def _build_conversation(label: str, filename: str, content: str) -> dict:
    return {"conversations": [
        {"from": "human", "value": clean_text(f"{label} for: {filename}")},
        {"from": "gpt",   "value": clean_content(content)},
    ]}

def scrape_url(url: str) -> dict | None:
    """
    Fetch a single URL and, if it contains YAML (Ansible-style) or Python
    (Ansible module/plugin/script) content, return a training-conversation
    dict. Anything else is discarded.
    """
    try:
        original_url = url.strip()
        url = convert_to_raw_github_url(original_url)
        print(f"🌐 Scraping: {original_url}")
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        raw_text = resp.text.strip()

        is_direct_file = 'github.com' in original_url or url.endswith(CODE_EXTENSIONS)
        if is_direct_file:
            filename = original_url.split('/')[-1].split('?')[0]
            if is_html_content(raw_text):
                print(f"   ⚠️  Skipped: raw content looks like HTML → {filename}")
                return None

            if filename.endswith('.py'):
                if not is_python_code(raw_text, require_ansible_context=False):
                    print(f"   ⚠️  Skipped: not valid Python → {filename}")
                    return None
                print(f"   ✅ SUCCESS (Python) → {filename}")
                return _build_conversation("Ansible Python module/script", filename, raw_text)

            if not is_yaml_code(raw_text):
                print(f"   ⚠️  Skipped: no Ansible YAML detected → {filename}")
                return None
            print(f"   ✅ SUCCESS (YAML) → {filename}")
            return _build_conversation("Ansible Playbook", filename, raw_text)

        soup = BeautifulSoup(resp.text, 'html.parser')
        title_tag  = soup.find('title')
        page_title = title_tag.get_text(strip=True) if title_tag else "Ansible Guide"
        page_title = clean_text(re.sub(r'\s+', ' ', page_title)[:160])

        yaml_blocks = []
        python_blocks = []

        def _classify(block: str):
            if len(block) <= 100 or is_html_content(block):
                return
            if is_yaml_code(block):
                yaml_blocks.append(block)
            elif is_python_code(block, require_ansible_context=True):
                python_blocks.append(block)

        for pre in soup.find_all('pre'):
            code_tag = pre.find('code') or pre
            _classify(code_tag.get_text().strip())

        selectors = [
            'div.highlight', 'pre.highlight', 'div.code',
            'div.language-yaml', 'div.language-python', 'div.language-py',
        ]
        for selector in selectors:
            for el in soup.select(selector):
                _classify(el.get_text().strip())

        if not yaml_blocks and not python_blocks:
            print(f"   ⚠️  Skipped: no YAML/Python code found → {page_title}")
            return None
        if yaml_blocks:
            best_code = clean_content(max(yaml_blocks, key=len))
            if is_html_content(best_code):
                print(f"   ⚠️  Skipped: best block is HTML → {page_title}")
                return None
            print("   ✅ SUCCESS → YAML code extracted from page")
            return _build_conversation("Ansible Playbook", page_title, best_code)

        best_code = clean_content(max(python_blocks, key=len))
        if is_html_content(best_code):
            print(f"   ⚠️  Skipped: best block is HTML → {page_title}")
            return None
        print("   ✅ SUCCESS → Python code extracted from page")
        return _build_conversation("Ansible Python module/script", page_title, best_code)

    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None

if __name__ == "__main__":
    if not os.path.exists(INPUT_FILE):
        with open(INPUT_FILE, 'w', encoding='utf-8') as f:
            f.write(
                "# One URL per line. Lines starting with # are ignored.\n"
                "# GitHub links (repo root, /tree/branch/subdir, or /blob/file)\n"
                "# are expanded automatically to every .yml/.yaml/.py file found,\n"
                "# including files in subdirectories.\n"
            )
        print(f"Created {INPUT_FILE} — add your URLs and re-run.")
    else:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            seed_urls = [l.strip() for l in f if l.strip() and not l.strip().startswith('#')]
        print(f"📋 Loaded {len(seed_urls)} seed URL(s)\n")

        dataset = []
        for i, seed_url in enumerate(seed_urls, 1):
            print(f"[{i}/{len(seed_urls)}] {seed_url}")
            urls_to_scrape = gather_urls_to_scrape(seed_url)
            if not urls_to_scrape:
                print("   ⚠️  Nothing to scrape from this URL (no YAML/Python found/linked)\n")
                continue
            for url in urls_to_scrape:
                result = scrape_url(url)
                if result:
                    dataset.append(result)
                time.sleep(1.5)
            print()

        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, indent=2, ensure_ascii=False)
        print(f"\n🎉 Done! Saved {len(dataset)} conversations → {OUTPUT_FILE}")
