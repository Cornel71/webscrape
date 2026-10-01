import argparse
import whois


def main():
    parser = argparse.ArgumentParser(description="Perform a WHOIS lookup")
    parser.add_argument("domain", help="Domain name, e.g. example.ro")
    args = parser.parse_args()

    try:
        result = whois.whois(args.domain)

        print(f"Domain: {result.domain_name}")
        print(f"Registrar: {result.registrar}")
        print(f"Creation date: {result.creation_date}")
        print(f"Expiration date: {result.expiration_date}")
        print(f"Name servers: {result.name_servers}")

    except Exception as error:
        print(f"WHOIS lookup failed: {error}")


if __name__ == "__main__":
    main()
