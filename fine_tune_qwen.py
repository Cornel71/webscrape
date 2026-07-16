import os
import json
import torch
import datasets
import re
from unsloth import FastLanguageModel
from datasets import Dataset
from trl import SFTTrainer
from transformers import TrainingArguments
from unsloth import get_chat_template

class FakeHasher:
    def __init__(self, *args, **kwargs): pass
    def update(self, *args, **kwargs): pass
    def hash(self, *args, **kwargs): return "python314_fixed_hash"
    def hexdigest(self, *args, **kwargs): return "53a45da08ea554c162af631c80e64ebe"

datasets.fingerprint.Hasher = FakeHasher
datasets.fingerprint.generate_fingerprint = lambda *args, **kwargs: "53a45da08ea554c162af631c80e64ebe"

os.environ["PYTORCH_ALLOC_CONF"] = "expandable_segments:True,max_split_size_mb:128"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
max_seq_length = 2048
dtype = None
load_in_4bit = True    # Mandatory in 4-bit for optimized out-of-the-box BnB usage

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name = "unsloth/Qwen2.5-Coder-7B-Instruct-bnb-4bit",
    max_seq_length = max_seq_length,
    dtype = dtype,
    load_in_4bit = load_in_4bit,
)

model = FastLanguageModel.get_peft_model(
    model,
    r = 8,                 # Conservative value (8) to save memory on GPU matrices
    target_modules = ["q_proj", "v_proj", "k_proj", "o_proj"], # Essential layers for code attention modeling
    lora_alpha = 16,
    lora_dropout = 0.0,    # Unsloth works optimally with 0.0 dropout during training
    bias = "none",
    use_gradient_checkpointing = "unsloth", # Ultra-efficient native Gradient Checkpointing
    random_state = 3407,
)

tokenizer = get_chat_template(
    tokenizer,
    chat_template = "qwen-2.5",
    mapping = {"role" : "from", "content" : "value", "user" : "human", "assistant" : "gpt"},
)

def formatting_prompts_func(examples):
    conversations = examples["conversations"]
    texts = [tokenizer.apply_chat_template(convo, tokenize=False, add_generation_prompt=False) for convo in conversations]
    return { "text" : texts }

json_file_path = "finetune_dataset_chat.json"
valid_conversations = []
removed_elements_count = 0

print(f"[INFO] Analyzing database from GitLab: '{json_file_path}' (20 MB)...")

try:
    with open(json_file_path, "r", encoding="utf-8") as f:
        parsed_data = json.load(f)
        if isinstance(parsed_data, list):
            for item in parsed_data:
                if isinstance(item, dict) and "conversations" in item:
                    convo = item["conversations"]
                    if len(convo) >= 2 and convo[0]["from"] == "human" and convo[1]["from"] == "gpt":
                        human_text = str(convo[0]["value"])
                        gpt_text = str(convo[1]["value"])
                        human_cleaned = re.sub(r'[^\x20-\x7E\n\r\t\u00A0-\u017F]', '', human_text).strip()
                        gpt_cleaned = re.sub(r'[^\x20-\x7E\n\r\t\u00A0-\u017F]', '', gpt_text).strip()
                        if len(gpt_cleaned) < 10:
                            removed_elements_count += 1
                            continue
                        new_conversation = [
                            {
                                "from": "system",
                                "value": "You are a Senior DevOps Engineer and an expert in Ansible automation. Generate only clean, valid YAML code that is well-indented and idempotent."
                            },
                            {"from": "human", "value": human_cleaned},
                            {"from": "gpt", "value": gpt_cleaned}
                        ]
                        valid_conversations.append(new_conversation)
                    else:
                        removed_elements_count += 1
                else:
                    removed_elements_count += 1
except Exception as e:
    print(f"[CRITICAL ERROR] Failed to read JSON file: {e}")

print(f"[INFO] Successfully loaded {len(valid_conversations)} samples for Qwen.")
print(f"[INFO] Removed {removed_elements_count} invalid or too short elements.")

if len(valid_conversations) == 0:
    raise ValueError("Error: The final dataset is empty. Check the structure of your JSON file!")

dataset = Dataset.from_dict({"conversations": valid_conversations})
dataset = dataset.map(formatting_prompts_func, batched = True)

trainer = SFTTrainer(
    model = model,
    tokenizer = tokenizer,
    train_dataset = dataset,
    dataset_text_field = "text",
    max_seq_length = max_seq_length,
    packing = False, 
    args = TrainingArguments(
        per_device_train_batch_size = 1,     # Kept at 1 to prevent OutOfMemory on the 7B model
        gradient_accumulation_steps = 8,     # Accumulates gradients for a stable update
        optim = "adamw_torch",
        learning_rate = 3e-5,                # Stable learning rate for Qwen Coder models
        num_train_epochs = 3,                # Running 3 full epochs across your entire 20MB file
        warmup_ratio = 0.05,
        fp16 = False,                        # Disabled: Qwen 2.5 natively requires bfloat16
        bf16 = True,                         # Enabled for native hardware precision on RTX series
        logging_steps = 5,
        output_dir = "gitlab_code_results",
        report_to = "none",
        weight_decay = 0.05,
        lr_scheduler_type = "cosine",
        save_strategy = "no",
    ),
)

print("\n[START] Launching training process for Qwen2.5-Coder-7B...")
trainer.train()

print("\n[INFO] Merging LoRA adapters and saving final 16-bit Qwen model...")
model.save_pretrained_merged("native_ansible_model", tokenizer, save_method = "merged_16bit")

print("\n🚀 [COMPLETE SUCCESS] Qwen-2.5-Coder training has finished!")
print("Your new smart code model is saved in the local folder: 'native_ansible_model'")
