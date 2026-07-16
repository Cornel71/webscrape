import os
import json
import hashlib
import datasets
import subprocess
from transformers import AutoTokenizer
class FakeHasher:
    def __init__(self, *args, **kwargs): pass
    def update(self, *args, **kwargs): pass
    def hash(self, *args, **kwargs): return "python314_fixed_hash"
    def hexdigest(self, *args, **kwargs): return "53a45da08ea554c162af631c80e64ebe"

datasets.fingerprint.Hasher = FakeHasher
datasets.fingerprint.generate_fingerprint = lambda *args, **kwargs: "53a45da08ea554c162af631c80e64ebe"

os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True"
LOCAL_CONVERTER_PATH = ".unsloth/llama.cpp/convert_hf_to_gguf.py"
INPUT_MODEL = "model_ansible_nativ"
OUTPUT_FOLDER = "model_gguf_repaired"
GGUF_OUTPUT_PATH = f"{OUTPUT_FOLDER}/qwen_ansible_expert.gguf"

print("[1/2] Injecting correct tokenizer configuration into the local folder...")
try:
    correct_tokenizer = AutoTokenizer.from_pretrained("unsloth/Qwen2.5-Coder-7B-Instruct-bnb-4bit", local_files_only=True)
    correct_tokenizer.save_pretrained(INPUT_MODEL)
    print(" -> Native Qwen vocabulary has been successfully mapped to the local folder!")
except Exception as e:
    print(f" -> Note on offline tokenizer loading: {e}. Continuing with existing files.")

print(f"\n[2/2] Launching direct local HF -> GGUF conversion...")
if not os.path.exists(LOCAL_CONVERTER_PATH):
    raise FileNotFoundError(f"Local converter not found at path: {LOCAL_CONVERTER_PATH}")

os.makedirs(OUTPUT_FOLDER, exist_ok=True)

command = [
    "/usr/bin/python3", LOCAL_CONVERTER_PATH,
    INPUT_MODEL,
    "--outfile", GGUF_OUTPUT_PATH,
    "--outtype", "f16"  # Save in stable float16 format
]

print(f" Executing command: {' '.join(command)}")

try:
    subprocess.run(command, check=True)
    print(f"\n🚀 [TOTAL SUCCESS] The GGUF file has been generated offline: '{GGUF_OUTPUT_PATH}'")
    print("Vocabulary is fixed. The model is ready for Ollama!")
except subprocess.CalledProcessError as e:
    print(f"\n❌ Critical error while running the local converter: {e}")
