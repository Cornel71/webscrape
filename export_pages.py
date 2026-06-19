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
        print(f"{C_INFO}{timestamp}{C_RESET} {C_SUCCESS}{C_BOLD}[✔] {message}{C_RESET}")
    elif log_type == "warning":
        print(f"{C_INFO}{timestamp}{C_RESET} {C_WARN}[WARN] {message}{C_RESET}")
    elif log_type == "error":
        print(f"{C_INFO}{timestamp}{C_RESET} {C_ERROR}{C_BOLD}[ERR] {message}{C_RESET}")
    else:
        print(f"{C_INFO}{timestamp}{C_RESET} [INFO] {message}")

def determine_category(subject: str) -> str:
    """Classifies the subject into an explicit category based on keywords for structured groupings."""
    sub_lower = subject.lower()
    if any(keyword in sub_lower for keyword in ["docker", "container", "compose"]):
        return "DOCKER & CONTAINERS"
    elif any(keyword in sub_lower for keyword in ["nginx", "proxy", "ssl", "certbot"]):
        return "NGINX & WEB REVERSE PROXY"
    elif any(keyword in sub_lower for keyword in ["security", "hardening", "ssh", "sudoers", "ufw", "firewalld", "vault", "cyber"]):
        return "SECURITY & HARDENING"
    elif any(keyword in sub_lower for keyword in ["lvm", "storage", "partition", "filesystem"]):
        return "STORAGE & LVM MANAGEMENT"
    elif any(keyword in sub_lower for keyword in ["postgres", "postgresql", "database", "db"]):
        return "POSTGRESQL DATABASE MANAGEMENT"
    elif any(keyword in sub_lower for keyword in ["gitlab", "pipeline", "ci/cd", "ci"]):
        return "GITLAB CI/CD PIPELINES"
    elif any(keyword in sub_lower for keyword in ["sysctl", "kernel", "optimize", "pipelining", "performance"]):
        return "PERFORMANCE & KERNEL OPTIMIZATION"
    else:
        return "GENERAL AUTOMATION & SYSTEM TOOLS"

def load_file_status():
    """Checks file configurations and loads pre-existing output URLs to ensure duplicate suppression."""
    if not os.path.exists(SEARCH_SUBJECTS_FILE):
        log(f"The file '{SEARCH_SUBJECTS_FILE}' was not found. Generating a demo file...", "warning")
        examples = [
            "Idempotent playbook configuration for LVM logical volume management",
            "Nginx reverse proxy docker compose yaml configuration",
            "Create systemd service for python script on Linux"
        ]
        with open(SEARCH_SUBJECTS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(examples))
    with open(SEARCH_SUBJECTS_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f.readlines() if line.strip()]
    log(f"Subjects detected in '{SEARCH_SUBJECTS_FILE}': {len(lines)}", "info")

    existing_links = set()
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    clean_line = line.strip()
                    if clean_line.startswith("http"):
                        existing_links.add(clean_line)
            log(f"The existing file stores {len(existing_links)} unique links. Avoid duplicates enabled.", "success")
        except Exception:
            log("The destination output file could not be parsed properly.", "warning")
    else:
        log("The destination file is new and will be populated on the fly.", "info")
    return lines, existing_links

def simplify_subject_for_search(text: str) -> str:
    """Cleans up connecting filler words or complex phrases for simpler web queries."""
    short_text = text.lower()
    filler_phrases = [
        "ansible playbook configuration for", "performance optimization and pipelining guide for",
        "security hardening best practices in", "error handling with block, rescue, and fail in",
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

def extract_top_3_links(subject: str, ddgs_instance: DDGS, existing_links: set) -> list:
    """Searches the internet and returns up to 3 valid, unique, non-blacklisted URLs."""
    optimized_query = simplify_subject_for_search(subject)
    found_links = []
    try:
        results = ddgs_instance.text(optimized_query, max_results=12)
        if results:
            for res in results:
                url = res.get('href', '')
                if url and url.startswith('http'):
                    parsed_url = urlparse(url)
                    domain = parsed_url.netloc.lower()
                    if any(bad_domain in domain for bad_domain in DOMAIN_BLACKLIST):
                        continue
                    if url not in found_links and url not in existing_links:
                        found_links.append(url)
                        if len(found_links) == 3:
                            break
    except Exception as e:
        log(f"   [!] Error during web search execution: {e}", "error")
    return found_links

def run_cloud_scraping():
    """Main CLI execution loop with automatic content categorization and anti-spam clearing."""
    subjects, existing_links = load_file_status()
    if not subjects:
        log("No subjects to process. The script will now stop.", "warning")
        return

    log(f"Starting collection of top links for each of the {len(subjects)} subjects...", "info")
    categorized_results = {}

    try:
        with DDGS() as ddgs:
            for index, subject in enumerate(subjects, 1):
                log(f"-> [{index}/{len(subjects)}] Searching for: '{subject}'", "info")
                links = extract_top_3_links(subject, ddgs, existing_links)
                if links:
                    category = determine_category(subject)
                    if category not in categorized_results:
                        categorized_results[category] = []
                    for link in links:
                        categorized_results[category].append(link)
                        existing_links.add(link)
                        log(f"   [+] Found ({category}): {link}", "success")
                else:
                    log("   [-] No new, safe, or unique links collected for this search phrase.", "warning")
                print("-" * 70)
                time.sleep(3)
        if categorized_results:
            with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                for cat, links in categorized_results.items():
                    f.write(f"\n=========================================\n")
                    f.write(f" 📂 CATEGORY: {cat}\n")
                    f.write(f"=========================================\n")
                    for link in links:
                        f.write(f"{link}\n")
            total_saved = sum(len(lst) for lst in categorized_results.values())
            log(f"Pipeline complete! {total_saved} clean links were categorized and appended to '{OUTPUT_FILE}'.", "success")
        else:
            log("No new links were added to the file in this run.", "warning")

    except Exception as general_error:
        log(f"General module communication failure inside DDGS wrapper: {general_error}", "error")

if __name__ == "__main__":
    if os.name == 'nt':
        os.system('color')
    run_cloud_scraping()
