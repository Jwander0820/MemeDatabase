import json
import os
import sys

def prune_discord_export(data):
    """
    Prunes the raw Discord export data according to the target schema:
    - Root level: Keep only dateRange, exportedAt, messages, messageCount.
    - Message level: Keep only id, type, timestamp, timestampEdited, callEndedTimestamp, isPinned, content, author, attachments.
    - Author level: Keep only id, name, discriminator, isBot.
    - Attachment level: Keep only id, url, fileName, fileSizeBytes.
    """
    root_keys = ["dateRange", "exportedAt", "messages", "messageCount"]
    pruned_root = {k: data[k] for k in root_keys if k in data}
    
    if "messages" in pruned_root and isinstance(pruned_root["messages"], list):
        message_keys = [
            "id", "type", "timestamp", "timestampEdited", 
            "callEndedTimestamp", "isPinned", "content", "author", "attachments"
        ]
        author_keys = ["id", "name", "discriminator", "isBot"]
        attachment_keys = ["id", "url", "fileName", "fileSizeBytes"]
        
        pruned_messages = []
        for msg in pruned_root["messages"]:
            if not isinstance(msg, dict):
                pruned_messages.append(msg)
                continue
            
            # Prune message level keys
            pruned_msg = {k: msg[k] for k in message_keys if k in msg}
            
            # Prune author level keys
            if "author" in pruned_msg and isinstance(pruned_msg["author"], dict):
                pruned_msg["author"] = {
                    k: pruned_msg["author"][k] for k in author_keys if k in pruned_msg["author"]
                }
                
            # Prune attachment level keys
            if "attachments" in pruned_msg and isinstance(pruned_msg["attachments"], list):
                pruned_attachments = []
                for att in pruned_msg["attachments"]:
                    if isinstance(att, dict):
                        pruned_att = {k: att[k] for k in attachment_keys if k in att}
                        pruned_attachments.append(pruned_att)
                    else:
                        pruned_attachments.append(att)
                pruned_msg["attachments"] = pruned_attachments
            
            pruned_messages.append(pruned_msg)
            
        pruned_root["messages"] = pruned_messages
        pruned_root["messageCount"] = len(pruned_messages)
        
    return pruned_root

def main():
    if len(sys.argv) < 2:
        print("Usage: python convert.py <input_json_path> [output_json_path]")
        sys.exit(1)
        
    input_path = sys.argv[1]
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        # Default output file name if not provided
        dir_name, file_name = os.path.split(input_path)
        base_name, ext = os.path.splitext(file_name)
        output_path = os.path.join(dir_name, f"{base_name}_fixed{ext}")
        
    if not os.path.exists(input_path):
        print(f"Error: Input file does not exist: {input_path}")
        sys.exit(1)
        
    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
    except Exception as e:
        print(f"Error reading JSON from {input_path}: {e}")
        sys.exit(1)
        
    fixed_data = prune_discord_export(raw_data)
    
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(fixed_data, f, indent=2, ensure_ascii=False)
        print(f"Successfully converted and saved to: {output_path}")
    except Exception as e:
        print(f"Error writing JSON to {output_path}: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
