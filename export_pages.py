import os
import time
from urllib.parse import urlparse

try:
    from ddgs import DDGS
except ImportError:
    print("[!] Error: The 'ddgs' library is missing.")
    print("    Install it via terminal using: pip install ddgs")
    raise


SEARCH_SUBJECTS_FILE = "search_subjects.txt"
OUTPUT_FILE = "saved_links.txt"

DOMAIN_BLACKLIST = [
    "bing.com",
    "linkedin.com",
    "x.com",
    "khanacademy.org",
    "facebook.com",
    "oneuptime.com",
    "youtube.com",
    "reddit.com",
    "adclick.g.doubleclick.net",
    "outbrain.com",
    "taboola.com",
    "pinterest.com",
    "sponsored-link.com"
]

C_RESET = "\033[0m"
C_INFO = "\033[94m"
C_SUCCESS = "\033[92m"
C_WARN = "\033[93m"
C_ERROR = "\033[91m"
C_BOLD = "\033[1m"


def log(message, log_type="info"):
    """Displays formatted and colored logs in the terminal with a timestamp."""
    timestamp = time.strftime("[%H:%M:%S]")

    if log_type == "success":
        print(
            f"{C_INFO}{timestamp}{C_RESET} "
            f"{C_SUCCESS}{C_BOLD}[✔] {message}{C_RESET}"
        )
    elif log_type == "warning":
        print(
            f"{C_INFO}{timestamp}{C_RESET} "
            f"{C_WARN}[WARN] {message}{C_RESET}"
        )
    elif log_type == "error":
        print(
            f"{C_INFO}{timestamp}{C_RESET} "
            f"{C_ERROR}{C_BOLD}[ERR] {message}{C_RESET}"
        )
    else:
        print(f"{C_INFO}{timestamp}{C_RESET} [INFO] {message}")


def load_file_status():
    """Loads search subjects and existing URLs."""
    if not os.path.exists(SEARCH_SUBJECTS_FILE):
        log(
            f"The file '{SEARCH_SUBJECTS_FILE}' was not found. "
            "Generating a demo file...",
            "warning"
        )

        examples = [
            "Idempotent playbook configuration for LVM logical volume management",
            "Nginx reverse proxy docker compose yaml configuration",
            "Create systemd service for python script on Linux"
        ]

        with open(SEARCH_SUBJECTS_FILE, "w", encoding="utf-8") as file:
            file.write("\n".join(examples))

    with open(SEARCH_SUBJECTS_FILE, "r", encoding="utf-8") as file:
        subjects = [
            line.strip()
            for line in file.readlines()
            if line.strip()
        ]

    log(
        f"Subjects detected in '{SEARCH_SUBJECTS_FILE}': "
        f"{len(subjects)}",
        "info"
    )

    existing_links = set()

    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as file:
                for line in file:
                    clean_line = line.strip()

                    if clean_line.startswith("http"):
                        existing_links.add(clean_line)

            log(
                f"The existing file stores {len(existing_links)} "
                "unique links. Avoid duplicates enabled.",
                "success"
            )
        except Exception:
            log(
                "The destination output file could not be parsed properly.",
                "warning"
            )
    else:
        log(
            "The destination file is new and will be populated on the fly.",
            "info"
        )

    return subjects, existing_links


def simplify_subject_for_search(text: str) -> str:
    """Simplifies the subject for a shorter web search query."""
    short_text = text.lower()

    filler_phrases = [
        "ansible playbook configuration for",
        "performance optimization and pipelining guide for",
        "security hardening best practices in",
        "error handling with block, rescue, and fail in",
        "implementing rolling updates and zero-downtime deployment for",
        "correct syntax and yaml indentation best practices for",
        "advanced loops usage and map or select filters in",
        "automated security auditing and compliance checking for",
        "detecting indicators of compromise (ioc) and anomalous behavior in",
        "incident response automation and threat mitigation for"
    ]

    for phrase in filler_phrases:
        short_text = short_text.replace(phrase, "")

    if "ansible" not in short_text:
        short_text = "ansible " + short_text

    return " ".join(short_text.split()[:6]).strip()


def extract_first_link(
    subject: str,
    ddgs_instance: DDGS,
    existing_links: set
):
    """Returns the first valid, unique, non-blacklisted URL."""
    optimized_query = simplify_subject_for_search(subject)

    try:
        results = ddgs_instance.text(
            optimized_query,
            max_results=12
        )

        for result in results:
            url = result.get("href", "")

            if not url or not url.startswith("http"):
                continue

            parsed_url = urlparse(url)
            domain = parsed_url.netloc.lower()

            if any(
                blocked_domain in domain
                for blocked_domain in DOMAIN_BLACKLIST
            ):
                continue

            if url in existing_links:
                continue

            return url

    except Exception as error:
        log(
            f"Error during web search execution: {error}",
            "error"
        )

    return None


def run_cloud_scraping():
    """Searches each subject and saves only the first valid link."""
    subjects, existing_links = load_file_status()

    if not subjects:
        log(
            "No subjects to process. The script will now stop.",
            "warning"
        )
        return

    log(
        f"Starting collection of the first link for each of the "
        f"{len(subjects)} subjects...",
        "info"
    )

    links_to_save = []

    try:
        with DDGS() as ddgs:
            for index, subject in enumerate(subjects, 1):
                log(
                    f"-> [{index}/{len(subjects)}] "
                    f"Searching for: '{subject}'",
                    "info"
                )

                link = extract_first_link(
                    subject,
                    ddgs,
                    existing_links
                )

                if link:
                    links_to_save.append(link)
                    existing_links.add(link)

                    log(
                        f"Found first link: {link}",
                        "success"
                    )
                else:
                    log(
                        "No new, safe, or unique link found.",
                        "warning"
                    )

                print("-" * 70)
                time.sleep(3)

        if links_to_save:
            with open(OUTPUT_FILE, "a", encoding="utf-8") as file:
                for link in links_to_save:
                    file.write(f"{link}\n")

            log(
                f"Pipeline complete! {len(links_to_save)} first links "
                f"were appended to '{OUTPUT_FILE}'.",
                "success"
            )
        else:
            log(
                "No new links were added to the file in this run.",
                "warning"
            )

    except Exception as general_error:
        log(
            "General module communication failure inside DDGS wrapper: "
            f"{general_error}",
            "error"
        )


if __name__ == "__main__":
    if os.name == "nt":
        os.system("color")

    run_cloud_scraping()
