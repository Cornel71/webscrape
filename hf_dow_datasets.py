import os
import json
import re
from huggingface_hub import HfApi, hf_hub_download, list_repo_files

def download_json_datasets(topic_keyword, max_results=2, download_dir="./hf_datasets"):
    """
    Searches HF datasets by keyword and downloads ONLY JSON/JSONL format data files.
    """
    print(f"🔍 Searching for datasets related to: '{topic_keyword}'...")
    api = HfApi()
    try:
        search_results = api.list_datasets(
            search=topic_keyword,
            limit=max_results,
            sort="downloads"
        )
    except Exception as e:
        print(f"❌ Failed to query Hugging Face Hub: {e}")
        return []
    dataset_ids = [dataset.id for dataset in search_results]
    if not dataset_ids:
        print("❌ No datasets found matching that topic.")
        return []

    print(f"📚 Found {len(dataset_ids)} matching datasets: {dataset_ids}")
    downloaded_files = []

    for repo_id in dataset_ids:
        print(f"\n⏳ Inspecting repository: {repo_id}...")
        safe_name = repo_id.replace("/", "_")
        repo_download_dir = os.path.join(download_dir, safe_name)
        try:
            all_files = list_repo_files(repo_id=repo_id, repo_type="dataset")
            json_files = [f for f in all_files if f.lower().endswith(('.json', '.jsonl'))]

            if not json_files:
                print(f"⏭️ Skipping {repo_id}: No JSON files found.")
                continue

            os.makedirs(repo_download_dir, exist_ok=True)
            print(f"🎯 Found {len(json_files)} JSON file(s). Starting explicit download...")
            for file_path in json_files:
                print(f"   ⬇️ Downloading JSON: {file_path}")
                local_file = hf_hub_download(
                    repo_id=repo_id,
                    filename=file_path,
                    repo_type="dataset",
                    local_dir=repo_download_dir
                )
                downloaded_files.append(local_file)
            print(f"✅ Successfully saved files to: {repo_download_dir}")
        except Exception as e:
            print(f"⚠️ Could not pull files from {repo_id}. Error: {e}")
    return downloaded_files


def transform_to_sharegpt(downloaded_files, output_file="ansible_python_dataset.json", max_human_chars=300):
    """
    Transforms downloaded raw JSON files into structured ShareGPT conversation lists.
    Enforces a strict maximum character limit on human prompts and strips huge code blocks.
    """
    if not downloaded_files:
        print("\n❌ No files available for transformation.")
        return

    print(f"\n🔄 Transforming {len(downloaded_files)} files into ShareGPT format...")
    transformed_data = []
    invalid_endings = (" i", " th", " an", " becau", " wit", " thos", " the")

    for file_path in downloaded_files:
        records = []
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            if not content:
                continue

            try:
                parsed_json = json.loads(content)
                records = parsed_json if isinstance(parsed_json, list) else [parsed_json]
            except json.JSONDecodeError as je:
                if "Extra data" in str(je):
                    records = []
                    for line in content.splitlines():
                        if line.strip():
                            records.append(json.loads(line))
                else:
                    raise je

            file_survived_count = 0
            for item in records:
                question = item.get("question", "").strip()
                answer = item.get("answer", "").strip()
                if not question or not answer:
                    continue
                if answer.endswith(invalid_endings):
                    continue
                if "```python" in question.lower():
                    question = question.split("```python")[0].strip()
                elif "```" in question:
                    question = question.split("```")[0].strip()
                question = question.replace("\\n", " ").replace("\n", " ").replace("\\t", " ")
                question = re.sub(r'\s+', ' ', question).strip()
                if len(question) < 5:
                    question = "Python code analysis for Ansible/Django configurations."
                if len(question) > max_human_chars:
                    truncated = question[:max_human_chars].strip()
                    if " " in truncated:
                        question = truncated.rsplit(" ", 1)[0].strip() + "..."
                    else:
                        question = truncated + "..."
                if len(answer) > 0 and answer[-1] not in ('.', '!', '?', '"', '`', '}', ']', ':', 'o', 'k'):
                    if " " in answer and len(answer.split()[-1]) < 3:
                        continue 
                conversation_block = {
                    "conversations": [
                        {"from": "human", "value": question},
                        {"from": "gpt", "value": answer}
                    ]
                }
                transformed_data.append(conversation_block)
                file_survived_count += 1
            print(f"   ✅ Processed: {os.path.basename(file_path)} ({file_survived_count} records recovered)")
        except Exception as e:
            print(f"   ⚠️ Error converting file {os.path.basename(file_path)}: {e}")
    if transformed_data:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(transformed_data, f, indent=2, ensure_ascii=False)
        print(f"\n🎉 Process Complete! {len(transformed_data)} clean entries saved to: {output_file}")
    else:
        print("\n❌ No records survived the cleanup and mapping stage.")


if __name__ == "__main__":
    TARGET_TOPIC = "ansible"
    OUTPUT_NAME = "ansible_python_dataset.json"
    raw_files = download_json_datasets(topic_keyword=TARGET_TOPIC, max_results=300)
    transform_to_sharegpt(downloaded_files=raw_files, output_file=OUTPUT_NAME, max_human_chars=300)
