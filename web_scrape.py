import json
import requests
from bs4 import BeautifulSoup
import re
import time
import os
from urllib.parse import urlparse

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/128.0 Safari/537.36'
}

INPUT_FILE = "saved_links.txt"
OUTPUT_FILE = "ansible_python_dataset.json"


def convert_to_raw_github_url(url):
    if 'github.com' not in url:
        return url
    if '/blob/' in url:
        raw_url = url.replace('/blob/', '/raw/')
        print(f"   🔄 Converted blob → raw")
        return raw_url
    return url


def is_binary(resp):
    content_type = resp.headers.get('Content-Type', '').lower()
    if any(x in content_type for x in ['pdf', 'image', 'octet-stream']):
        return True
    return resp.content.startswith(b'%PDF') or resp.content.startswith(b'\x89PNG')


def contains_strong_code(text):
    indicators = [
        '---', 'hosts:', 'tasks:', 'ansible.', 'become:', 'register:',
        'when:', 'template:', 'package:', 'service:', 'import ', 
        'def ', 'Ansible', 'playbook', 'role:'
    ]
    return any(ind in text for ind in indicators)


def clean_content(text):
    text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]', '', text)
    return text.strip()


def scrape_url(url):
    try:
        original_url = url
        url = convert_to_raw_github_url(url)
        
        print(f"🌐 Scraping: {original_url}")
        
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        
        if is_binary(resp):
            print("   ❌ Skipped: Binary file (PDF/Image)")
            return None
        
        content = resp.text.strip()
        
        # ===================== GITHUB RAW =====================
        if 'github.com' in original_url or url.endswith(('.yml', '.yaml', '.py')):
            filename = original_url.split('/')[-1].split('?')[0]
            cleaned = clean_content(content)
            
            if not contains_strong_code(cleaned) and len(cleaned) < 200:
                print("   ⚠️  Skipped: Not enough code signals")
                return None
                
            prompt = f"Configurare Ansible Playbook idempotent pentru: {filename}"
            if filename.endswith('.py'):
                prompt = f"Configurare script Python idempotent pentru: {filename}"
            
            print(f"   ✅ SUCCESS (GitHub) → {filename}")
            return {"conversations": [
                {"from": "human", "value": prompt},
                {"from": "gpt", "value": cleaned}
            ]}
        
        # ===================== BLOG / DOCS =====================
        else:
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # Get clean title
            title_tag = soup.find('title')
            page_title = title_tag.get_text(strip=True) if title_tag else "Ansible Guide"
            page_title = re.sub(r'\s+', ' ', page_title)[:150]
            
            # Extract code blocks - IMPROVED
            code_blocks = []
            for pre in soup.find_all('pre'):
                code = pre.find('code') or pre
                block = code.get_text().strip()
                
                if len(block) > 120 and contains_strong_code(block):
                    code_blocks.append(block)
            
            # Also try <code> tags inside pre or divs with highlight
            for code in soup.find_all('code'):
                block = code.get_text().strip()
                if len(block) > 150 and contains_strong_code(block):
                    code_blocks.append(block)
            
            if not code_blocks:
                print(f"   ⚠️  Skipped: No valid code blocks found → {page_title}")
                return None
            
            # Take the largest and cleanest block
            best_code = max(code_blocks, key=len)
            best_code = clean_content(best_code)
            
            prompt = f"Configurare Ansible Playbook idempotent pentru: {page_title}"
            
            print(f"   ✅ SUCCESS → Extracted code from blog/page")
            return {"conversations": [
                {"from": "human", "value": prompt},
                {"from": "gpt", "value": best_code}
            ]}
            
    except Exception as e:
        print(f"   ❌ Error: {e}")
        return None


# ===================== MAIN =====================
if __name__ == "__main__":
    if not os.path.exists(INPUT_FILE):
        print(f"❌ {INPUT_FILE} not found! Creating template...")
        with open(INPUT_FILE, 'w', encoding='utf-8') as f:
            f.write("# Put your URLs here\n")
            f.write("# GitHub blob links are auto-converted to raw\n\n")
        exit(1)
    
    with open(INPUT_FILE, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
    
    print(f"📋 Loaded {len(urls)} URLs\n")
    
    dataset = []
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}]")
        result = scrape_url(url)
        if result:
            dataset.append(result)
        time.sleep(1.5)
    
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump({"data": dataset}, f, indent=2, ensure_ascii=False)
    
    print(f"\n🎉 Finished! Saved {len(dataset)} valid conversations to → {OUTPUT_FILE}")
