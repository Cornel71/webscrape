"""
Collect Ansible YAML and Python code from URLs and create a JSON dataset.

Supported sources:
- Regular documentation pages containing code blocks
- GitHub repository URLs
- GitHub branch/folder URLs
- GitHub individual file URLs

The generated dataset uses this structure:

[
    {
        "conversations": [
            {"from": "human", "value": "..."},
            {"from": "gpt", "value": "..."}
        ]
    }
]
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import requests
import yaml
from bs4 import BeautifulSoup
from requests import Session


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INPUT_FILE = Path("saved_links.txt")
OUTPUT_FILE = Path("ansible_yaml_dataset.json")

REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 30

# The token is optional. It helps avoid GitHub API rate limits.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

CODE_EXTENSIONS = (".yml", ".yaml", ".py")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    )
}


# Configure application logging.
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regular expressions and detection constants
# ---------------------------------------------------------------------------

HTML_TAG_RE = re.compile(r"<[a-zA-Z][^>]{0,200}>")
DOCTYPE_RE = re.compile(r"<!DOCTYPE\s", re.IGNORECASE)
HTML_OPEN_RE = re.compile(r"<html[\s>]", re.IGNORECASE)

GITHUB_URL_RE = re.compile(
    r"github\.com/"
    r"(?P<owner>[^/]+)/"
    r"(?P<repo>[^/]+?)(?:\.git)?"
    r"(?:/(?:tree|blob)/"
    r"(?P<branch>[^/]+)"
    r"(?:/(?P<path>[^?#]*))?"
    r")?"
    r"/?(?:[?#].*)?$"
)

ANSIBLE_DICT_KEYS = {
    "hosts",
    "tasks",
    "vars",
    "roles",
    "handlers",
    "become",
    "gather_facts",
    "name",
    "collections",
    "pre_tasks",
    "post_tasks",
    "vars_files",
    "block",
    "rescue",
    "always",
}

PYTHON_ANSIBLE_MARKERS = (
    "AnsibleModule",
    "ansible.module_utils",
    "ansible_collections",
    "from ansible",
    "import ansible",
    "DOCUMENTATION =",
    "DOCUMENTATION=",
    "EXAMPLES =",
    "EXAMPLES=",
    "RETURN =",
    "RETURN=",
    "ActionModule",
    "CallbackBase",
    "LookupBase",
    "FilterModule",
)

UNICODE_TRANSLATION_TABLE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "--",
        "\u2015": "--",
        "\u2026": "...",
        "\u00a0": " ",
        "\u00ad": "",
        "\u200b": "",
        "\u200c": "",
        "\u200d": "",
        "\ufeff": "",
    }
)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def create_session() -> Session:
    """
    Create and configure a reusable HTTP session.

    A session reuses TCP connections, which is more efficient than creating
    a new connection for every request.
    """
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def github_api_headers() -> dict[str, str]:
    """
    Return HTTP headers required for GitHub API requests.

    A GitHub token is added only when GITHUB_TOKEN is available.
    """
    headers = {
        **DEFAULT_HEADERS,
        "Accept": "application/vnd.github+json",
    }

    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    return headers


def fetch_text(
    session: Session,
    url: str,
    *,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
    headers: dict[str, str] | None = None,
) -> str:
    """
    Download a URL and return its response body as text.

    Args:
        session: Reusable HTTP session.
        url: URL to download.
        timeout: Maximum number of seconds to wait.
        headers: Optional request-specific headers.

    Returns:
        The response body.

    Raises:
        requests.RequestException:
            If the request fails or returns an HTTP error status.
    """
    response = session.get(
        url,
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.text


# ---------------------------------------------------------------------------
# Text and content validation
# ---------------------------------------------------------------------------

def is_html_content(text: str) -> bool:
    """
    Return True when text appears to be an HTML document.

    This prevents an error page or regular web page from being saved as
    YAML or Python code.
    """
    stripped = text.lstrip()

    if DOCTYPE_RE.match(stripped):
        return True

    if HTML_OPEN_RE.match(stripped):
        return True

    # Check a short sample for multiple HTML tags.
    sample = stripped[:400]
    return len(HTML_TAG_RE.findall(sample)) > 4


def clean_text(text: str) -> str:
    """
    Normalize Unicode punctuation and remove surrounding whitespace.

    For example, smart quotes are converted into regular ASCII quotes.
    """
    return text.translate(UNICODE_TRANSLATION_TABLE).strip()


def clean_content(text: str) -> str:
    """
    Remove control characters and trailing whitespace from code.

    Empty lines are removed, while the relative indentation of remaining
    lines is preserved.
    """
    # Remove non-printable control characters, except newline and tab.
    text = re.sub(
        r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]",
        "",
        text,
    )

    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip()
    ]

    return clean_text("\n".join(lines))


def is_yaml_code(text: str) -> bool:
    """
    Return True if text is valid YAML with Ansible-like structure.

    The function accepts both:
    - A mapping, such as a playbook or variables file.
    - A list of mappings, such as a list of Ansible plays or tasks.
    """
    stripped = text.strip()

    if not stripped:
        return False

    try:
        parsed = yaml.safe_load(stripped)
    except yaml.YAMLError:
        return False

    if isinstance(parsed, dict):
        return bool(set(parsed) & ANSIBLE_DICT_KEYS)

    if isinstance(parsed, list):
        for item in parsed:
            if not isinstance(item, dict):
                continue

            keys = set(item)

            # Look for common Ansible keys.
            if keys & ANSIBLE_DICT_KEYS:
                return True

            # Many task names are module names, for example:
            # ansible.builtin.copy or community.general.debug.
            if any(
                isinstance(key, str)
                and (key.islower() or "." in key)
                for key in keys
            ):
                return True

    return False


def is_python_code(
    text: str,
    *,
    require_ansible_context: bool = True,
) -> bool:
    """
    Return True if text is syntactically valid Python.

    Args:
        text: Candidate Python source code.
        require_ansible_context:
            When True, the source must also contain an Ansible-related marker.
            This avoids collecting unrelated Python snippets from websites.

    Returns:
        True if the code should be treated as Ansible-related Python.
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

    return any(marker in stripped for marker in PYTHON_ANSIBLE_MARKERS)


# ---------------------------------------------------------------------------
# GitHub URL handling
# ---------------------------------------------------------------------------

def convert_to_raw_github_url(url: str) -> str:
    """
    Convert a GitHub web file URL into a raw-content URL.

    Example:

        https://github.com/user/repo/blob/main/playbook.yml

    becomes:

        https://github.com/user/repo/raw/main/playbook.yml
    """
    if "github.com" in url and "/blob/" in url:
        logger.info("   Converted GitHub blob URL to raw URL")
        return url.replace("/blob/", "/raw/")

    return url


def parse_github_url(
    url: str,
) -> tuple[str, str, str | None, str | None] | None:
    """
    Parse a GitHub URL.

    Returns:
        A tuple containing:

        (
            repository owner,
            repository name,
            branch or None,
            path or None,
        )

        Returns None if the URL cannot be parsed.
    """
    match = GITHUB_URL_RE.search(url.strip())

    if not match:
        return None

    return (
        match.group("owner"),
        match.group("repo"),
        match.group("branch"),
        match.group("path"),
    )


def get_default_branch(
    session: Session,
    owner: str,
    repo: str,
) -> str:
    """
    Retrieve the default branch for a GitHub repository.
    """
    api_url = f"https://api.github.com/repos/{owner}/{repo}"

    response = session.get(
        api_url,
        headers=github_api_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    return response.json()["default_branch"]


def list_repository_code_urls(
    session: Session,
    owner: str,
    repo: str,
    branch: str | None = None,
    subpath: str | None = None,
) -> list[str]:
    """
    Find all supported code files in a GitHub repository.

    The Git Trees API is used with recursive traversal, so files inside
    nested directories are included.

    Args:
        session: Reusable HTTP session.
        owner: GitHub repository owner.
        repo: GitHub repository name.
        branch: Branch to inspect. Defaults to the repository's default branch.
        subpath: Optional folder restriction.

    Returns:
        Raw GitHub URLs for YAML and Python files.
    """
    if not branch:
        branch = get_default_branch(session, owner, repo)

    api_url = (
        f"https://api.github.com/repos/{owner}/{repo}"
        f"/git/trees/{branch}?recursive=1"
    )

    response = session.get(
        api_url,
        headers=github_api_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()

    data = response.json()

    if data.get("truncated"):
        logger.warning(
            "   Warning: GitHub returned a truncated repository tree"
        )

    normalized_subpath = subpath.strip("/") if subpath else None
    raw_base_url = (
        f"https://raw.githubusercontent.com/"
        f"{owner}/{repo}/{branch}/"
    )

    code_urls: list[str] = []

    for item in data.get("tree", []):
        if item.get("type") != "blob":
            continue

        path = item.get("path", "")

        if normalized_subpath:
            is_inside_subpath = (
                path == normalized_subpath
                or path.startswith(f"{normalized_subpath}/")
            )

            if not is_inside_subpath:
                continue

        if path.endswith(CODE_EXTENSIONS):
            code_urls.append(raw_base_url + path)

    return code_urls


def expand_github_url(
    session: Session,
    url: str,
) -> list[str]:
    """
    Expand a GitHub URL into one or more raw code URLs.

    Behavior:

    - Repository URL:
        Returns all YAML and Python files in the repository.

    - Tree/folder URL:
        Returns supported files under that folder.

    - Blob/file URL:
        Returns only that file if it has a supported extension.

    - Unsupported file:
        Returns an empty list.
    """
    parsed_url = parse_github_url(url)

    if not parsed_url:
        logger.warning("   Could not parse GitHub URL: %s", url)
        return []

    owner, repo, branch, path = parsed_url
    is_blob_url = "/blob/" in url

    if is_blob_url:
        if path and path.endswith(CODE_EXTENSIONS):
            return [convert_to_raw_github_url(url)]

        logger.warning("   Skipped unsupported GitHub file: %s", url)
        return []

    location = f"/{path}" if path else ""

    logger.info(
        "   Traversing GitHub repository %s/%s%s",
        owner,
        repo,
        location,
    )

    try:
        code_urls = list_repository_code_urls(
            session=session,
            owner=owner,
            repo=repo,
            branch=branch,
            subpath=path,
        )
    except requests.RequestException as error:
        logger.error("   Failed to list GitHub repository: %s", error)
        return []

    logger.info("   Found %d supported file(s)", len(code_urls))
    return code_urls


def gather_urls_to_scrape(
    session: Session,
    input_url: str,
) -> list[str]:
    """
    Convert one input URL into the URLs that should be downloaded.

    GitHub repository URLs are expanded. Other URLs are returned unchanged
    and are processed as documentation pages.
    """
    if "github.com" in input_url:
        return expand_github_url(session, input_url)

    return [input_url]


# ---------------------------------------------------------------------------
# Dataset creation
# ---------------------------------------------------------------------------

def build_conversation(
    label: str,
    filename: str,
    content: str,
) -> dict[str, list[dict[str, str]]]:
    """
    Build one training conversation for the output dataset.

    Args:
        label: Description shown in the human message.
        filename: File name or page title.
        content: Code to store in the assistant message.

    Returns:
        A dictionary containing one conversation.
    """
    return {
        "conversations": [
            {
                "from": "human",
                "value": clean_text(f"{label} for: {filename}"),
            },
            {
                "from": "gpt",
                "value": clean_content(content),
            },
        ]
    }


def classify_code_block(
    block: str,
    yaml_blocks: list[str],
    python_blocks: list[str],
) -> None:
    """
    Classify a scraped code block as YAML or Python.

    The block is appended to the appropriate list when it appears to contain
    useful Ansible code.
    """
    cleaned_block = block.strip()

    if len(cleaned_block) <= 100:
        return

    if is_html_content(cleaned_block):
        return

    if is_yaml_code(cleaned_block):
        yaml_blocks.append(cleaned_block)
    elif is_python_code(
        cleaned_block,
        require_ansible_context=True,
    ):
        python_blocks.append(cleaned_block)


def extract_code_blocks(
    html: str,
) -> tuple[list[str], list[str]]:
    """
    Extract Ansible YAML and Python code blocks from an HTML page.

    Returns:
        A tuple:

        (
            YAML blocks,
            Python blocks,
        )
    """
    soup = BeautifulSoup(html, "html.parser")

    yaml_blocks: list[str] = []
    python_blocks: list[str] = []

    # Standard documentation pages commonly place code in <pre><code>.
    for pre_element in soup.find_all("pre"):
        code_element = pre_element.find("code") or pre_element
        classify_code_block(
            code_element.get_text().strip(),
            yaml_blocks,
            python_blocks,
        )

    # Additional selectors support common documentation themes.
    selectors = (
        "div.highlight",
        "pre.highlight",
        "div.code",
        "div.language-yaml",
        "div.language-python",
        "div.language-py",
    )

    for selector in selectors:
        for element in soup.select(selector):
            classify_code_block(
                element.get_text().strip(),
                yaml_blocks,
                python_blocks,
            )

    return yaml_blocks, python_blocks


def get_page_title(html: str) -> str:
    """
    Extract and normalize the HTML page title.
    """
    soup = BeautifulSoup(html, "html.parser")
    title_element = soup.find("title")

    if not title_element:
        return "Ansible Guide"

    title = title_element.get_text(strip=True)
    title = re.sub(r"\s+", " ", title)

    return clean_text(title[:160])


def scrape_direct_code_file(
    original_url: str,
    raw_text: str,
) -> dict[str, list[dict[str, str]]] | None:
    """
    Validate and convert a directly downloaded YAML or Python file.

    GitHub raw files and URLs ending in a supported extension are handled
    through this function.
    """
    filename = original_url.split("/")[-1].split("?")[0]

    if is_html_content(raw_text):
        logger.warning(
            "   Skipped: downloaded content looks like HTML: %s",
            filename,
        )
        return None

    if filename.endswith(".py"):
        if not is_python_code(
            raw_text,
            require_ansible_context=False,
        ):
            logger.warning("   Skipped: invalid Python: %s", filename)
            return None

        logger.info("   Accepted Python file: %s", filename)

        return build_conversation(
            "Ansible Python module/script",
            filename,
            raw_text,
        )

    if not is_yaml_code(raw_text):
        logger.warning(
            "   Skipped: YAML does not appear to be Ansible YAML: %s",
            filename,
        )
        return None

    logger.info("   Accepted YAML file: %s", filename)

    return build_conversation(
        "Ansible Playbook",
        filename,
        raw_text,
    )


def scrape_documentation_page(
    html: str,
) -> dict[str, list[dict[str, str]]] | None:
    """
    Extract the largest valid Ansible code block from an HTML page.

    YAML is preferred when both YAML and Python blocks are present.
    """
    page_title = get_page_title(html)
    yaml_blocks, python_blocks = extract_code_blocks(html)

    if not yaml_blocks and not python_blocks:
        logger.warning(
            "   Skipped: no Ansible YAML or Python code found: %s",
            page_title,
        )
        return None

    if yaml_blocks:
        best_block = max(yaml_blocks, key=len)

        logger.info(
            "   Accepted YAML block from page: %s",
            page_title,
        )

        return build_conversation(
            "Ansible Playbook",
            page_title,
            best_block,
        )

    best_block = max(python_blocks, key=len)

    logger.info(
        "   Accepted Python block from page: %s",
        page_title,
    )

    return build_conversation(
        "Ansible Python module/script",
        page_title,
        best_block,
    )


def scrape_url(
    session: Session,
    url: str,
) -> dict[str, list[dict[str, str]]] | None:
    """
    Download and process one URL.

    The URL is treated as a direct code file when it is:
    - A GitHub URL, or
    - A URL ending in .yml, .yaml, or .py.

    All other URLs are treated as documentation pages.
    """
    original_url = url.strip()
    download_url = convert_to_raw_github_url(original_url)

    logger.info("Scraping: %s", original_url)

    try:
        response_text = fetch_text(session, download_url)
    except requests.RequestException as error:
        logger.error("   Request failed: %s", error)
        return None

    is_direct_file = (
        "github.com" in original_url
        or download_url.lower().endswith(CODE_EXTENSIONS)
    )

    if is_direct_file:
        return scrape_direct_code_file(
            original_url,
            response_text,
        )

    return scrape_documentation_page(response_text)


# ---------------------------------------------------------------------------
# Input and output helpers
# ---------------------------------------------------------------------------

def create_input_file_if_missing() -> bool:
    """
    Create a starter input file when one does not already exist.

    Returns:
        True if a new file was created, otherwise False.
    """
    if INPUT_FILE.exists():
        return False

    INPUT_FILE.write_text(
        "# One URL per line. Lines starting with # are ignored.\n"
        "# GitHub repository, tree, or blob URLs are supported.\n",
        encoding="utf-8",
    )

    logger.info(
        "Created %s. Add URLs and run the script again.",
        INPUT_FILE,
    )

    return True


def load_seed_urls() -> list[str]:
    """
    Read valid URLs from the input file.

    Blank lines and lines beginning with '#' are ignored.
    """
    lines = INPUT_FILE.read_text(encoding="utf-8").splitlines()

    return [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]


def save_dataset(
    dataset: list[dict[str, list[dict[str, str]]]],
) -> None:
    """
    Save the collected conversations as formatted JSON.
    """
    OUTPUT_FILE.write_text(
        json.dumps(
            dataset,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """
    Run the complete collection process.
    """
    if create_input_file_if_missing():
        return

    seed_urls = load_seed_urls()
    logger.info("Loaded %d seed URL(s)\n", len(seed_urls))

    dataset: list[dict[str, list[dict[str, str]]]] = []
    scraped_urls: set[str] = set()

    with create_session() as session:
        for index, seed_url in enumerate(seed_urls, start=1):
            logger.info(
                "[%d/%d] %s",
                index,
                len(seed_urls),
                seed_url,
            )

            urls_to_scrape = gather_urls_to_scrape(
                session,
                seed_url,
            )

            if not urls_to_scrape:
                logger.warning("   Nothing to scrape\n")
                continue

            for url in urls_to_scrape:
                # Avoid downloading the same file more than once.
                if url in scraped_urls:
                    logger.info("   Skipping duplicate URL: %s", url)
                    continue

                scraped_urls.add(url)

                result = scrape_url(session, url)

                if result:
                    dataset.append(result)

                time.sleep(REQUEST_DELAY_SECONDS)

            logger.info("")

    save_dataset(dataset)

    logger.info(
        "Done! Saved %d conversation(s) to %s",
        len(dataset),
        OUTPUT_FILE,
    )


if __name__ == "__main__":
    main()
