import os
import json
import hashlib
import urllib.parse
import requests

HOST_URL = "http://gitlab.alienware.loc"
REPO_PATH = "root/ansible"
BRANCH = "main"
OUTPUT_JSON = "finetune_dataset_chat_gitlab.json"
GITLAB_TOKEN = ""
ALLOWED_EXTENSIONS = {'.yml', '.yaml', '.py', '.sh'}
IGNORED_FILES = {'.gitignore', '.env', 'package.json', 'requirements.txt'}
PROJECT_ID_ENCODED = urllib.parse.quote_plus(REPO_PATH)
API_BASE_URL = f"{HOST_URL}/api/v4/projects/{PROJECT_ID_ENCODED}"
HEADERS = {"PRIVATE-TOKEN": GITLAB_TOKEN}

def generate_devops_question(file_path, file_ext):
    """Generates a realistic natural language prompt based on the file type."""
    base_name = file_path.split("/")[-1]
    if file_ext in {'.yml', '.yaml'}:
        if "playbook" in file_path.lower() or "site" in file_path.lower():
            return f"Show me the complete Ansible playbook configuration for '{base_name}' and explain its deployment steps."
        elif "role" in file_path.lower() or "tasks" in file_path.lower():
            return f"Provide the Ansible tasks configuration from the role file '{file_path}' to automate this infrastructure component."
        else:
            return f"Review and display the Ansible YAML configuration content for '{base_name}'."
    elif file_ext == '.sh':
        return f"Provide the bash deployment script from file '{base_name}' used in the automation pipeline."
    elif file_ext == '.py':
        return f"Explain and show the Python script from file '{base_name}' used for infrastructure utility or testing."
    return f"Display the contents and purpose of the automation file '{base_name}'."

def get_all_files():
    """Recursively fetches the list of all files from the repository via API."""
    valid_files = []
    page = 1
    per_page = 100
    print("Accessing repository structure from GitLab...")
    while True:
        url_tree = f"{API_BASE_URL}/repository/tree"
        params = {
            "ref": BRANCH,
            "recursive": True,
            "page": page,
            "per_page": per_page
        }
        response = requests.get(url_tree, headers=HEADERS, params=params, timeout=30)
        response.raise_for_status()
        items = response.json()
        if not items:
            break
        for item in items:
            if item["type"] == "blob":
                file_path = item["path"]
                file_name = item["name"]
                _, ext = os.path.splitext(file_name)
                ext = ext.lower()
                if ext in ALLOWED_EXTENSIONS and file_name not in IGNORED_FILES:
                    valid_files.append((file_path, ext))
        page += 1
        if len(items) < per_page:
            break
    return valid_files

def download_file_content(file_path):
    """Downloads the raw content of a specific file from GitLab."""
    encoded_path = urllib.parse.quote_plus(file_path)
    url_raw = f"{API_BASE_URL}/repository/files/{encoded_path}/raw"
    params = {"ref": BRANCH}
    response = requests.get(url_raw, headers=HEADERS, params=params, timeout=30)
    response.raise_for_status()
    return response.text

dataset = []
try:
    files_to_process = get_all_files()
    print(f"Identified {len(files_to_process)} valid DevOps files in GitLab. Starting content download...")
    for idx, (file_path, ext) in enumerate(files_to_process, 1):
        try:
            content = download_file_content(file_path)
            if not content.strip():
                continue
            chunk_id = hashlib.md5(f"{file_path}_gitlab_full".encode('utf-8')).hexdigest()
            human_question = generate_devops_question(file_path, ext)
            item = {
                "id": chunk_id,
                "conversations": [
                    {"from": "human", "value": human_question},
                    {"from": "gpt", "value": content}
                ],
                "metadata": {
                    "repo": REPO_PATH,
                    "file": file_path,
                    "chunk": 0,
                    "total_chunks_in_file": 1,
                    "type": "code" if ext in {'.py', '.sh'} else "ansible_yaml",
                    "size": len(content.encode('utf-8')),
                    "sha": hashlib.sha1(content.encode('utf-8')).hexdigest(),
                    "url": f"{HOST_URL}/{REPO_PATH}/-/blob/{BRANCH}/{file_path}",
                    "source": f"gitlab:{REPO_PATH}:{file_path}",
                    "branch": BRANCH,
                    "host": HOST_URL
                }
            }
            dataset.append(item)
            print(f"[{idx}/{len(files_to_process)}] Successfully downloaded and processed: {file_path}")
        except Exception as file_err:
            print(f"[ERROR] Failed to download file {file_path}: {file_err}")
    with open(OUTPUT_JSON, "w", encoding="utf-8") as out:
        json.dump(dataset, out, indent=2, ensure_ascii=False)
    print(f"\n[SUCCESS] Dataset completely generated from GitLab API!")
    print(f"-> Unique DevOps files exported: {len(dataset)}")
    print(f"-> File saved as: '{OUTPUT_JSON}'")

except Exception as global_err:
    print(f"\n[CRITICAL ERROR] Could not run API connection: {global_err}")
    print("Verify that your token permissions (api, read_api) and branch name are correct.")
