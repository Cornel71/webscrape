"""
Search for Ansible-related resources with SerpApi and save unique links.

Input:
    search_subjects.txt

Output:
    saved_links.txt

Each non-empty line in search_subjects.txt is treated as a search query.
Up to three new, non-blacklisted links are saved for each query.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import urlparse
import serpapi

SEARCH_SUBJECTS_FILE = Path("search_subjects.txt")
OUTPUT_FILE = Path("saved_links.txt")

SERPAPI_API_KEY = os.getenv("SERPAPI_KEY")

MAX_RESULTS_FROM_SEARCH = 12
MAX_LINKS_PER_SUBJECT = 3
REQUEST_DELAY_SECONDS = 3

DOMAIN_BLACKLIST = {
    "oneuptime.com",
    "youtube.com",
    "reddit.com",
    "doubleclick.net",
    "outbrain.com",
    "taboola.com",
    "pinterest.com",
    "sponsored-link.com",
}


C_RESET = "\033[0m"
C_INFO = "\033[94m"
C_SUCCESS = "\033[92m"
C_WARNING = "\033[93m"
C_ERROR = "\033[91m"
C_BOLD = "\033[1m"


def log(message: str, log_type: str = "info") -> None:
    """
    Print a timestamped, color-coded message.

    Args:
        message: Message to display.
        log_type: One of "info", "success", "warning", or "error".
    """
    timestamp = time.strftime("[%H:%M:%S]")

    prefix = f"{C_INFO}{timestamp}{C_RESET}"

    if log_type == "success":
        print(
            f"{prefix} "
            f"{C_SUCCESS}{C_BOLD}[OK] {message}{C_RESET}"
        )
    elif log_type == "warning":
        print(
            f"{prefix} "
            f"{C_WARNING}[WARN] {message}{C_RESET}"
        )
    elif log_type == "error":
        print(
            f"{prefix} "
            f"{C_ERROR}{C_BOLD}[ERROR] {message}{C_RESET}"
        )
    else:
        print(f"{prefix} [INFO] {message}")


def create_subject_file_if_missing() -> None:
    """
    Create a sample subject file when the input file does not exist.

    The function does not overwrite an existing file.
    """
    if SEARCH_SUBJECTS_FILE.exists():
        return

    example_subjects = [
        "Idempotent Ansible playbook for LVM logical volume management",
        "Nginx reverse proxy Docker Compose YAML configuration",
        "Create a systemd service for a Python script on Linux",
    ]

    SEARCH_SUBJECTS_FILE.write_text(
        "\n".join(example_subjects) + "\n",
        encoding="utf-8",
    )

    log(
        f"Created '{SEARCH_SUBJECTS_FILE}'. "
        "Review the subjects and run the script again.",
        "warning",
    )


def load_subjects() -> list[str]:
    """
    Read search subjects from the input file.

    Blank lines are ignored. Lines beginning with '#' are treated as
    comments and ignored.

    Returns:
        A list of search subjects.
    """
    create_subject_file_if_missing()

    lines = SEARCH_SUBJECTS_FILE.read_text(
        encoding="utf-8",
    ).splitlines()

    subjects = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith("#")
    ]

    log(
        f"Loaded {len(subjects)} subject(s) from "
        f"'{SEARCH_SUBJECTS_FILE}'."
    )

    return subjects


def load_existing_links() -> set[str]:
    """
    Load URLs already saved in the output file.

    Only lines beginning with 'http' are considered URLs. This also allows
    the function to ignore old separator lines or comments in an existing
    output file.

    Returns:
        A set of previously saved URLs.
    """
    if not OUTPUT_FILE.exists():
        log(
            f"'{OUTPUT_FILE}' does not exist. "
            "A new output file will be created."
        )
        return set()

    existing_links: set[str] = set()

    for line in OUTPUT_FILE.read_text(
        encoding="utf-8",
    ).splitlines():
        link = line.strip()

        if link.startswith(("http://", "https://")):
            existing_links.add(link)

    log(
        f"Loaded {len(existing_links)} existing link(s). "
        "Duplicate filtering is enabled.",
        "success",
    )

    return existing_links


def append_links(links: list[str]) -> None:
    """
    Append new URLs to the output file.

    Each URL is written on its own line. No category headers are added.
    """
    if not links:
        return

    with OUTPUT_FILE.open("a", encoding="utf-8") as output_file:
        for link in links:
            output_file.write(f"{link}\n")


def simplify_subject_for_search(subject: str) -> str:
    """
    Simplify a subject before sending it to the search engine.

    This removes repetitive phrases and limits the query length while
    ensuring that Ansible-related searches include the word "ansible".
    """
    query = subject.lower()

    filler_phrases = (
        "ansible playbook configuration for",
        "performance optimization and pipelining guide for",
        "security hardening best practices in",
        "error handling with block, rescue, and fail in",
        "implementing rolling updates and zero-downtime deployment for",
        "correct syntax and yaml indentation best practices for",
        "advanced loops usage and map or select filters in",
        "automated security auditing and compliance checking for",
        "detecting indicators of compromise (ioc) and anomalous behavior in",
        "incident response automation and threat mitigation for",
    )

    for phrase in filler_phrases:
        query = query.replace(phrase, "")

    if "ansible" not in query:
        query = f"ansible {query}"

    return " ".join(query.split()[:6])

def get_hostname(url: str) -> str:
    """
    Extract a normalized hostname from a URL.

    Returns:
        Hostname without the leading 'www.' prefix.
    """
    hostname = urlparse(url).hostname or ""
    return hostname.lower().removeprefix("www.")


def is_blacklisted_domain(url: str) -> bool:
    """
    Return True if a URL belongs to a blacklisted domain.

    Both the exact domain and its subdomains are rejected. For example,
    'www.reddit.com' and 'old.reddit.com' both match 'reddit.com'.
    """
    hostname = get_hostname(url)

    return any(
        hostname == blocked_domain
        or hostname.endswith(f".{blocked_domain}")
        for blocked_domain in DOMAIN_BLACKLIST
    )


def is_valid_result_url(url: str) -> bool:
    """
    Check whether a search result URL is suitable for saving.

    A valid URL must:
    - Use HTTP or HTTPS.
    - Have a hostname.
    - Not belong to a blacklisted domain.
    """
    parsed_url = urlparse(url)

    if parsed_url.scheme not in {"http", "https"}:
        return False

    if not parsed_url.netloc:
        return False

    return not is_blacklisted_domain(url)


def create_serpapi_client() -> serpapi.Client:
    """
    Create a SerpApi client using the SERPAPI_KEY environment variable.

    Raises:
        RuntimeError:
            If the API key is not configured.
    """
    if not SERPAPI_API_KEY:
        raise RuntimeError(
            "SERPAPI_KEY is not set. "
            "Set it before running the script."
        )

    return serpapi.Client(api_key=SERPAPI_API_KEY)


def search_for_links(
    client: serpapi.Client,
    subject: str,
    existing_links: set[str],
) -> list[str]:
    """
    Search Google through SerpApi and return new valid URLs.

    Args:
        client: Configured SerpApi client.
        subject: Original search subject.
        existing_links: URLs already saved or found during this run.

    Returns:
        Up to MAX_LINKS_PER_SUBJECT new URLs.
    """
    query = simplify_subject_for_search(subject)

    logger_message = f"Searching for: '{query}'"
    log(logger_message)

    try:
        results = client.search(
            engine="google",
            q=query,
            num=MAX_RESULTS_FROM_SEARCH,
            hl="en",
        )
    except Exception as error:
        log(f"SerpApi search failed: {error}", "error")
        return []

    organic_results = results.get("organic_results", [])
    new_links: list[str] = []

    for result in organic_results:
        url = result.get("link", "").strip()

        if not url:
            continue

        if not is_valid_result_url(url):
            continue

        if url in existing_links or url in new_links:
            continue

        new_links.append(url)

        if len(new_links) >= MAX_LINKS_PER_SUBJECT:
            break

    return new_links


def run_link_collection() -> None:
    """
    Execute the complete search and link-collection workflow.

    The function:
    1. Loads search subjects.
    2. Loads previously saved URLs.
    3. Searches each subject through SerpApi.
    4. Removes duplicates and blacklisted domains.
    5. Appends new URLs to saved_links.txt.
    6. Prints a final summary.
    """
    subjects = load_subjects()

    if not subjects:
        log("No search subjects were found. Exiting.", "warning")
        return

    existing_links = load_existing_links()
    all_new_links: list[str] = []

    try:
        client = create_serpapi_client()
    except RuntimeError as error:
        log(str(error), "error")
        return

    log(
        f"Starting searches for {len(subjects)} subject(s)."
    )

    for index, subject in enumerate(subjects, start=1):
        log(
            f"[{index}/{len(subjects)}] "
            f"Processing subject: {subject}"
        )

        links = search_for_links(
            client=client,
            subject=subject,
            existing_links=existing_links,
        )

        if not links:
            log("No new valid links found.", "warning")
        else:
            append_links(links)
            all_new_links.extend(links)
            existing_links.update(links)

            for link in links:
                log(f"Saved: {link}", "success")

        if index < len(subjects):
            time.sleep(REQUEST_DELAY_SECONDS)

        print("-" * 70)

    log("Collection complete.", "success")
    log(f"Subjects processed: {len(subjects)}")
    log(f"New links saved: {len(all_new_links)}")
    log(f"Total unique links in output file: {len(existing_links)}")
    log(f"Output file: {OUTPUT_FILE}")


if __name__ == "__main__":
    if os.name == "nt":
        os.system("color")

    run_link_collection()
