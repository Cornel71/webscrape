import os
import time

# Flexible DDGS import to ensure compatibility with the duckduckgo_search package
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        print("[!] Error: The 'duckduckgo_search' or 'ddgs' library is not installed.")
        print("    Run in your terminal: pip install duckduckgo_search")
        raise

# --- FILE CONFIGURATIONS ---
SEARCH_SUBJECTS_FILE = "search_subjects.txt"
OUTPUT_FILE = "saved_links.txt"  # Saves data as plain text (URLs only)

# ANSI Terminal Colors
C_RESET = "\033[0m"
C_INFO = "\033[94m"     # Blue
C_SUCCESS = "\033[92m"  # Green
C_WARN = "\033[93m"     # Orange
C_ERROR = "\033[91m"    # Red
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

def load_file_status():
    """Checks the status of local files and creates dummy ones if they are missing."""
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

    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
                saved_lines = [line.strip() for line in f.readlines() if line.strip()]
            log(f"The existing TXT file already stores {len(saved_lines)} links.", "success")
        except Exception:
            log("The TXT file is empty or corrupted. Writing from scratch.", "warning")
    else:
        log("The TXT file is new and will be created during execution.", "info")
        
    return lines

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

def extract_top_3_links(subject: str, ddgs_instance: DDGS) -> list:
    """Searches the internet and returns the first 3 valid URLs found."""
    optimized_query = simplify_subject_for_search(subject)
    found_links = []
    
    try:
        # Request slightly more results to ensure filtering of invalid entries
        results = list(ddgs_instance.text(optimized_query, max_results=5))
        
        for res in results:
            url = res.get('href', '')
            # Verify if the URL is valid and not already added
            if url and url.startswith('http') and url not in found_links:
                found_links.append(url)
                if len(found_links) == 3:
                    break
                    
    except Exception as e:
        log(f"   [!] Error during web search execution: {e}", "error")
        
    return found_links

def run_cloud_scraping():
    """Main Command Line Interface (CLI) execution function."""
    subjects = load_file_status()
    if not subjects:
        log("No subjects to process. The script will now stop.", "warning")
        return

    log(f"Starting collection of the top 3 links for each of the {len(subjects)} subjects...", "info")
    total_saved_links = 0

    try:
        with DDGS() as ddgs:
            for index, subject in enumerate(subjects, 1):
                log(f"-> [{index}/{len(subjects)}] Searching for: '{subject}'", "info")
                
                # Directly extract the top 3 links
                links = extract_top_3_links(subject, ddgs)
                
                if links:
                    # Open the TXT file to append the discovered URLs
                    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
                        for link in links:
                            f.write(f"{link}\n")
                            total_saved_links += 1
                            log(f"   [+] Saved: {link}", "success")
                else:
                    log("   [-] Could not collect links for this specific subject.", "warning")
                
                print("-" * 70)
                # Short delay to prevent rate-limiting from DuckDuckGo
                time.sleep(3)

    except Exception as general_error:
        log(f"General error initialization failure inside the DDGS module: {general_error}", "error")

    log(f"Pipeline complete! A total of {total_saved_links} new links were saved to '{OUTPUT_FILE}'.", "success")

if __name__ == "__main__":
    # Force ANSI color activation on Windows Command Prompt if applicable
    if os.name == 'nt':
        os.system('color')
    run_cloud_scraping()
