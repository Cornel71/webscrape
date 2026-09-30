import csv
import ipaddress
import socket
import time
from urllib.parse import urlsplit, urlunsplit
import requests
from ddgs import DDGS


INPUT_FILE = "subjects.txt"
BLOCKED_DOMAINS_FILE = "blocked_domains.txt"
OUTPUT_FILE = "results.csv"
RESULTS_PER_SUBJECT = 20
DELAY_BETWEEN_SEARCHES = 3
DELAY_BETWEEN_IP_LOOKUPS = 1

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36")
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

def trim_url(url: str) -> str:
    """
    Remove tracking parameters and fragments from a URL.
    Preserve scheme, hostname, port, path, and useful query parameters.
    """
    if not url:
        return ""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parts = urlsplit(url)
        if not parts.hostname:
            return ""
        hostname = parts.hostname.lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        tracking_keys = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "gclid",
            "fbclid",
            "msclkid",
            "ref",
            "ref_src",
        }
        clean_query_parts = []
        if parts.query:
            for item in parts.query.split("&"):
                key = item.split("=", 1)[0].lower()
                if key not in tracking_keys:
                    clean_query_parts.append(item)
        clean_query = "&".join(clean_query_parts)
        netloc = hostname
        try:
            if parts.port:
                netloc = f"{hostname}:{parts.port}"
        except ValueError:
            return ""
        return urlunsplit(
            (
                parts.scheme.lower(),
                netloc,
                parts.path.rstrip("/") or "/",
                clean_query,
                "",
            )
        )
    except ValueError:
        return ""

def extract_domain(value: str) -> str:
    """
    Extract a hostname from either a URL or a plain domain.
    """
    if not value:
        return ""
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    try:
        hostname = urlsplit(value).hostname
        if not hostname:
            return ""
        hostname = hostname.lower().rstrip(".")
        if hostname.startswith("www."):
            hostname = hostname[4:]
        return hostname
    except ValueError:
        return ""

def load_blocked_domains(filename: str) -> set[str]:
    """
    Load blocked domains from a text file.

    Blank lines and lines beginning with # are ignored.
    Both domains and URLs are accepted.
    """
    blocked_domains = set()
    try:
        with open(filename, "r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                domain = extract_domain(line)
                if domain:
                    blocked_domains.add(domain)
    except FileNotFoundError:
        print(f"Blocklist not found: {filename}")
        print("Continuing without blocked domains.")
    return blocked_domains

def is_blocked_domain(domain: str, blocked_domains: set[str],) -> bool:
    """
    Block an exact domain and all of its subdomains.
    For example, blocking example.com also blocks:
        www.example.com
        shop.example.com
    But it does not block:
        example.com.evil-site.com
    """
    domain = domain.lower().rstrip(".")
    for blocked_domain in blocked_domains:
        blocked_domain = blocked_domain.lower().rstrip(".")
        if (
            domain == blocked_domain
            or domain.endswith("." + blocked_domain)
        ):
            return True
    return False


def resolve_domain(domain: str) -> list[str]:
    """
    Resolve A and AAAA records for a domain.
    """
    addresses = set()
    try:
        results = socket.getaddrinfo(
            domain,
            443,
            type=socket.SOCK_STREAM,
        )
        for result in results:
            ip_address = result[4][0]
            addresses.add(ip_address)
    except socket.gaierror:
        pass
    return sorted(addresses)

def is_public_ip(ip_address: str) -> bool:
    """
    Return True only for globally routable IP addresses.
    """
    try:
        ip = ipaddress.ip_address(ip_address)
        return ip.is_global
    except ValueError:
        return False

def empty_ip_result(error_message: str = "") -> dict:
    return {
        "ip_country": "",
        "ip_region": "",
        "ip_city": "",
        "ip_latitude": "",
        "ip_longitude": "",
        "asn": "",
        "isp": "",
        "organization": "",
        "hosting": "",
        "ip_lookup_error": error_message,
    }

def lookup_ip(ip_address: str) -> dict:
    """
    Look up approximate location and network information
    for a public IP address.
    """
    if not is_public_ip(ip_address):
        return empty_ip_result(
            "Private or non-global IP address"
        )
    try:
        response = session.get(
            f"https://ipwho.is/{ip_address}",
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("success") is False:
            return empty_ip_result(
                data.get("message", "IP lookup failed")
            )
        connection = data.get("connection") or {}
        return {
            "ip_country": data.get("country", ""),
            "ip_region": data.get("region", ""),
            "ip_city": data.get("city", ""),
            "ip_latitude": data.get("latitude", ""),
            "ip_longitude": data.get("longitude", ""),
            "asn": connection.get("asn", ""),
            "isp": connection.get("isp", ""),
            "organization": connection.get("org", ""),
            "hosting": data.get("hosting", ""),
            "ip_lookup_error": "",
        }
    except requests.RequestException as exc:
        return empty_ip_result(str(exc))
    except ValueError as exc:
        return empty_ip_result(
            f"Invalid JSON response: {exc}"
        )

def search_subject(subject: str) -> list[dict]:
    """
    Search the web for one subject.
    """
    try:
        with DDGS() as search_client:
            results = search_client.text(
                query=subject,
                region="us-en",
                safesearch="moderate",
                max_results=RESULTS_PER_SUBJECT,
            )
            return list(results)
    except Exception as exc:
        print(f"Search error for '{subject}': {exc}")
        return []

def main():
    blocked_domains = load_blocked_domains(
        BLOCKED_DOMAINS_FILE
    )
    print(
        f"Loaded {len(blocked_domains)} blocked domain(s)."
    )
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as file:
            subjects = [
                line.strip()
                for line in file
                if line.strip()
                and not line.lstrip().startswith("#")
            ]
    except FileNotFoundError:
        print(f"Missing input file: {INPUT_FILE}")
        return
    if not subjects:
        print(f"No subjects found in {INPUT_FILE}")
        return
    rows = []
    seen = set()
    for subject in subjects:
        print(f"\nSearching: {subject}")
        search_results = search_subject(subject)
        for result in search_results:
            title = result.get("title", "")
            original_url = result.get("href", "")
            clean_url = trim_url(original_url)
            domain = extract_domain(clean_url)
            if not domain:
                continue
            if is_blocked_domain(
                domain,
                blocked_domains,
            ):
                print(f"  Blocked domain: {domain}")
                continue
            unique_key = (
                subject.lower(),
                domain,
            )
            if unique_key in seen:
                continue
            seen.add(unique_key)
            print(f"  Checking: {domain}")
            ip_addresses = resolve_domain(domain)
            if not ip_addresses:
                rows.append(
                    {
                        "subject": subject,
                        "title": title,
                        "url": clean_url,
                        "domain": domain,
                        "ip_address": "",
                        "ip_country": "",
                        "ip_region": "",
                        "ip_city": "",
                        "ip_latitude": "",
                        "ip_longitude": "",
                        "asn": "",
                        "isp": "",
                        "organization": "",
                        "hosting": "",
                        "ip_lookup_error": (
                            "DNS resolution failed"
                        ),
                    }
                )
                continue
            for ip_address in ip_addresses:
                print(
                    f"    {ip_address}"
                )
                ip_info = lookup_ip(ip_address)
                row = {
                    "subject": subject,
                    "title": title,
                    "url": clean_url,
                    "domain": domain,
                    "ip_address": ip_address,
                    **ip_info,
                }
                rows.append(row)
                time.sleep(DELAY_BETWEEN_IP_LOOKUPS)
        time.sleep(DELAY_BETWEEN_SEARCHES)
    if not rows:
        print("\nNo results found.")
        return
    fieldnames = [
        "subject",
        "title",
        "url",
        "domain",
        "ip_address",
        "ip_country",
        "ip_region",
        "ip_city",
        "ip_latitude",
        "ip_longitude",
        "asn",
        "isp",
        "organization",
        "hosting",
        "ip_lookup_error",
    ]
    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"\nSaved {len(rows)} records to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
