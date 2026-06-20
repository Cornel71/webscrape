# Ansible & Python Dataset Scraper

[![Python Version](https://shields.io)](https://python.org)
[![License: MIT](https://shields.io)](https://opensource.org)
[![Built for LLMs](https://shields.io)]()

An automated pipeline designed to scrape web pages and GitHub repositories, extract clean Ansible Playbooks (`YAML`) or Python scripts, and structure them into a ShareGPT-style JSON dataset for LLM training and fine-tuning.

---

## ✨ Features

* **GitHub Code Converter**: Identifies standard GitHub blob URLs and switches them to raw content links automatically.
* **Strict Validation Engine**: Rejects HTML noise by scanning text samples for raw structural tags and doctype signatures.
* **Syntax Heuristics**: Confirms the existence of explicit Python elements or structural Ansible playbook indicators (`- name:`, `hosts:`, `vars:`).
* **Text Normalization**: Strips hidden control characters and converts complex Unicode marks (smart quotes, em-dashes) into plain ASCII.
* **Smart Web Parsing**: Fallbacks to `BeautifulSoup` parsing to scan nested code tags and standard syntax highlighter containers if a direct file stream is not available.
* **LLM-Ready Structure**: Outputs target files directly into paired human/gpt conversation threads.

---

## 🛠️ Requirements & Installation

This project requires **Python 3.10** or higher. 

Clone the repository and install the dependencies:

```bash
git clone https://github.com/Cornel71/webscrape.git
cd webscrape
pip install requests beautifulsoup4
```

---

## 🚀 Quick Start Guide

### 1. Populate the search subjects and run the script in order to generate links
Execute the script once to generate the links based on phrase search input file:
```bash
python export_pages.py
```

### 2. Execute the script to scrape the links saved
Execute the script once to generate the empty text input file:
```bash
python web_scrape.py
```


```text
# --- GitHub Raw Examples ---
https://github.com/Cornel71/webscrape/blob/main/search_subjects.txt
https://github.com/Cornel71/webscrape/blob/main/saved_links.txt
```

### 3. Build Your Dataset
Run the script again to extract, normalize, and compile the data:
```bash
python web_scrape.py
```

---

## ⚙️ Project Architecture

├── export_pages.py              # Main execution script to generate links based on search_subjects.txt
├── search_subjects.txt          # Input file for search subjects (Manual-generated)
├── web_scrape.py                # Main execution script to scrape web based on the links generated
├── saved_links.txt              # Input file for target URLs (Auto-generated)
└── ansible_python_dataset.json  # Compiled training dataset (Auto-generated)

### Script Configurations
You can adjust these parameters globally directly inside `scraper.py`:

| Constant Variable | Default Value | Purpose |
| :--- | :--- | :--- |
| `INPUT_FILE` | `"saved_links.txt"` | Defines the source text file read path. |
| `OUTPUT_FILE` | `"ansible_python_dataset.json"` | Target location for the structured JSON export. |
| `HEADERS` | `Mozilla/5.0...` | Custom browser signature string to prevent query blocks. |

---

## 📊 Dataset Export Format

The script structures extracted material inside an array containing standard conversational sequences:

```json
[
  {
    "conversations": [
      {
        "from": "human",
        "value": "Ansible Playbook for: site.yml"
      },
      {
        "from": "gpt",
        "value": "---\n- name: Configure Webservers\n  hosts: webservers\n  vars:\n    http_port: 80"
      }
    ]
  },
  {
    "conversations": [
      {
        "from": "human",
        "value": "Python script for: deploy.py"
      },
      {
        "from": "gpt",
        "value": "import os\nimport sys\n\ndef main():\n    print(\"Deploying system...\")"
      }
    ]
  }
]
```


## ⚖️ License

Distributed under the MIT License. See `LICENSE` for more information.
