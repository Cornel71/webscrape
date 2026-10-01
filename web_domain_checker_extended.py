import csv
import ipaddress
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit
import dns.exception
import dns.resolver
import requests
import tldextract
import whois
from ddgs import DDGS


INPUT_FILE = "subjects.txt"
BLOCKED_DOMAINS_FILE = "blocked_domains.txt"
OUTPUT_FILE = "results.csv"
RESULTS_PER_SUBJECT = 20
DELAY_BETWEEN_SEARCHES = 3
DELAY_BETWEEN_IP_LOOKUPS = 1
DELAY_BETWEEN_WHOIS_LOOKUPS = 2
DNS_TIMEOUT = 5
CHECK_DKIM = True
DKIM_SELECTORS = [
    "default", "google", "selector1", "selector2",
    "k1", "k2", "s1", "s2", "mail", "dkim", "smtp", "mandrill",
]
MAIL_PROVIDERS = {
    "google.com": "Google Workspace",
    "googlemail.com": "Google Workspace",
    "outlook.com": "Microsoft 365",
    "protection.outlook.com": "Microsoft 365",
    "pphosted.com": "Proofpoint",
    "mimecast.com": "Mimecast",
    "zoho.com": "Zoho Mail",
    "zoho.eu": "Zoho Mail",
    "protonmail.ch": "Proton Mail",
    "secureserver.net": "GoDaddy",
    "mailgun.org": "Mailgun",
    "messagingengine.com": "Fastmail",
    "yandex.net": "Yandex",
    "icloud.com": "iCloud",
    "improvmx.com": "ImprovMX",
    "forwardemail.net": "Forward Email",
}

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 13_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36")
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})

resolver = dns.resolver.Resolver()
resolver.timeout = DNS_TIMEOUT
resolver.lifetime = DNS_TIMEOUT
tld_extractor = tldextract.TLDExtract(suffix_list_urls=())
domain_info_cache: dict[str, dict] = {}


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

def get_registered_domain(domain: str) -> str:
    """
    Return the registrable domain (eTLD+1), e.g. blog.example.co.uk -> example.co.uk.
    WHOIS and mail records live at this level. Falls back to the input
    if it can't be determined (IP addresses, odd TLDs, etc.).
    """
    ext = tld_extractor(domain)
    return ext.registered_domain or domain

def _first(value):
    """WHOIS fields may be a single value or a list; return the first non-empty."""
    if isinstance(value, (list, tuple, set)):
        for item in value:
            if item:
                return item
        return ""
    return value or ""

def _to_datetime(value):
    value = _first(value)
    return value if isinstance(value, datetime) else None

def _fmt_date(value) -> str:
    dt = _to_datetime(value)
    return dt.strftime("%Y-%m-%d") if dt else ""

def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

def _join(value) -> str:
    if isinstance(value, (list, tuple, set)):
        items = sorted({str(v).strip().lower() for v in value if v})
        return "; ".join(items)
    return str(value).strip().lower() if value else ""

def empty_whois_result(error_message: str = "") -> dict:
    return {
        "registered_domain": "",
        "whois_registrar": "",
        "whois_created": "",
        "whois_updated": "",
        "whois_expires": "",
        "domain_age_days": "",
        "days_until_expiry": "",
        "whois_nameservers": "",
        "whois_status": "",
        "whois_registrant_org": "",
        "whois_registrant_country": "",
        "whois_error": error_message,
    }

def lookup_whois(registered_domain: str) -> dict:
    """
    WHOIS lookup for a registered domain.
    Note: many registrants are hidden by privacy/GDPR redaction,
    so org/country are often blank.
    """
    result = empty_whois_result()
    result["registered_domain"] = registered_domain
    try:
        data = whois.whois(registered_domain)
    except Exception as exc:
        result["whois_error"] = f"WHOIS failed: {exc}"
        return result

    if not data or not (data.get("domain_name") or data.get("registrar")):
        result["whois_error"] = "No WHOIS data returned"
        return result

    created = _to_datetime(data.get("creation_date"))
    expires = _to_datetime(data.get("expiration_date"))
    now = datetime.utcnow()

    result.update(
        {
            "whois_registrar": str(_first(data.get("registrar"))),
            "whois_created": _fmt_date(data.get("creation_date")),
            "whois_updated": _fmt_date(data.get("updated_date")),
            "whois_expires": _fmt_date(data.get("expiration_date")),
            "domain_age_days": (
                (now - _naive_utc(created)).days if created else ""
            ),
            "days_until_expiry": (
                (_naive_utc(expires) - now).days if expires else ""
            ),
            "whois_nameservers": _join(data.get("name_servers")),
            "whois_status": _join(
                [str(s).split()[0] for s in data.get("status") or []]
                if isinstance(data.get("status"), (list, tuple, set))
                else str(data.get("status") or "").split()[:1]
            ),
            "whois_registrant_org": str(_first(data.get("org"))),
            "whois_registrant_country": str(_first(data.get("country"))),
        }
    )
    return result

def get_txt_records(name: str) -> list[str]:
    """Return TXT records for a name, with multi-string records joined."""
    try:
        answers = resolver.resolve(name, "TXT")
    except (
        dns.resolver.NXDOMAIN,
        dns.resolver.NoAnswer,
        dns.resolver.NoNameservers,
        dns.exception.Timeout,
    ):
        return []
    records = []
    for rdata in answers:
        records.append(
            b"".join(rdata.strings).decode("utf-8", errors="replace")
        )
    return records

def get_mx_records(domain: str) -> tuple[list[tuple[int, str]], str]:
    """Return ([(preference, host), ...], error_message)."""
    try:
        answers = resolver.resolve(domain, "MX")
    except dns.resolver.NXDOMAIN:
        return [], "Domain does not exist"
    except dns.resolver.NoAnswer:
        return [], ""
    except dns.resolver.NoNameservers:
        return [], "No nameservers answered"
    except dns.exception.Timeout:
        return [], "DNS timeout"
    records = sorted(
        (r.preference, str(r.exchange).rstrip(".").lower()) for r in answers
    )
    return records, ""

def detect_mail_provider(mx_hosts: list[str]) -> str:
    providers = set()
    for host in mx_hosts:
        for suffix, name in MAIL_PROVIDERS.items():
            if host == suffix or host.endswith("." + suffix):
                providers.add(name)
    return "; ".join(sorted(providers))

def spf_policy(spf_record: str) -> str:
    """Classify the SPF 'all' mechanism."""
    for token in reversed(spf_record.lower().split()):
        if token.endswith("all") and token.lstrip("+-~?") == "all":
            return {
                "-": "fail (-all)",
                "~": "softfail (~all)",
                "?": "neutral (?all)",
                "+": "pass (+all, insecure)",
            }.get(token[0], "pass (+all, insecure)")
        if token.startswith("redirect="):
            return "redirect"
    return "no 'all' mechanism"

def dmarc_tag(record: str, tag: str) -> str:
    for part in record.split(";"):
        key, _, value = part.strip().partition("=")
        if key.strip().lower() == tag:
            return value.strip().lower()
    return ""

def empty_mail_result(error_message: str = "") -> dict:
    return {
        "has_mx": "",
        "mx_records": "",
        "mail_provider": "",
        "spf_record": "",
        "spf_policy": "",
        "dmarc_record": "",
        "dmarc_policy": "",
        "dmarc_rua": "",
        "dkim_selectors_found": "",
        "mail_error": error_message,
    }

def check_mail(domain: str) -> dict:
    """
    Check email configuration for a domain: MX, SPF, DMARC and (optionally)
    DKIM for a handful of common selectors.
    """
    result = empty_mail_result()

    mx_records, mx_error = get_mx_records(domain)
    result["mail_error"] = mx_error
    null_mx = len(mx_records) == 1 and mx_records[0][1] in ("", ".")
    result["has_mx"] = bool(mx_records) and not null_mx
    if null_mx:
        result["mx_records"] = "null MX (domain does not accept mail)"
    else:
        result["mx_records"] = "; ".join(
            f"{pref} {host}" for pref, host in mx_records
        )
        result["mail_provider"] = detect_mail_provider(
            [host for _, host in mx_records]
        )

    spf_records = [
        r for r in get_txt_records(domain)
        if r.lower().startswith("v=spf1")
    ]
    if spf_records:
        result["spf_record"] = spf_records[0]
        result["spf_policy"] = spf_policy(spf_records[0])
        if len(spf_records) > 1:
            result["spf_policy"] += " [multiple SPF records - invalid]"

    dmarc_records = [
        r for r in get_txt_records(f"_dmarc.{domain}")
        if r.lower().startswith("v=dmarc1")
    ]
    if dmarc_records:
        result["dmarc_record"] = dmarc_records[0]
        result["dmarc_policy"] = dmarc_tag(dmarc_records[0], "p")
        result["dmarc_rua"] = dmarc_tag(dmarc_records[0], "rua")

    if CHECK_DKIM:
        found = []
        for selector in DKIM_SELECTORS:
            records = get_txt_records(f"{selector}._domainkey.{domain}")
            if any("v=dkim1" in r.lower() or "p=" in r.lower() for r in records):
                found.append(selector)
        result["dkim_selectors_found"] = "; ".join(found)

    return result


def get_domain_info(domain: str) -> dict:
    """
    WHOIS + mail checks for the registrable domain of `domain`, cached so
    each registered domain is only queried once per run.
    """
    registered = get_registered_domain(domain)
    if registered in domain_info_cache:
        return domain_info_cache[registered]

    print(f"    WHOIS/mail: {registered}")
    whois_info = lookup_whois(registered)
    time.sleep(DELAY_BETWEEN_WHOIS_LOOKUPS)
    mail_info = check_mail(registered)

    info = {**whois_info, **mail_info}
    domain_info_cache[registered] = info
    return info


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

FIELDNAMES = [
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
    "registered_domain",
    "whois_registrar",
    "whois_created",
    "whois_updated",
    "whois_expires",
    "domain_age_days",
    "days_until_expiry",
    "whois_nameservers",
    "whois_status",
    "whois_registrant_org",
    "whois_registrant_country",
    "whois_error",
    "has_mx",
    "mx_records",
    "mail_provider",
    "spf_record",
    "spf_policy",
    "dmarc_record",
    "dmarc_policy",
    "dmarc_rua",
    "dkim_selectors_found",
    "mail_error",
]

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

            base = {
                "subject": subject,
                "title": title,
                "url": clean_url,
                "domain": domain,
            }
            domain_info = get_domain_info(domain)
            ip_addresses = resolve_domain(domain)
            if not ip_addresses:
                rows.append(
                    {
                        **base,
                        "ip_address": "",
                        **empty_ip_result("DNS resolution failed"),
                        **domain_info,
                    }
                )
                continue
            for ip_address in ip_addresses:
                print(
                    f"    {ip_address}"
                )
                ip_info = lookup_ip(ip_address)
                rows.append(
                    {
                        **base,
                        "ip_address": ip_address,
                        **ip_info,
                        **domain_info,
                    }
                )
                time.sleep(DELAY_BETWEEN_IP_LOOKUPS)
        time.sleep(DELAY_BETWEEN_SEARCHES)
    if not rows:
        print("\nNo results found.")
        return
    with open(
        OUTPUT_FILE,
        "w",
        newline="",
        encoding="utf-8",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=FIELDNAMES,
        )
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"\nSaved {len(rows)} records to {OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
