# V10.20.1 — AI Training and Windows Server Network Configuration

## Train AI

Customer AI Evaluation now includes **Train AI** for System Administrators.

The workflow:
1. Checks for `ollama.exe` and runs `ollama --version` / `ollama list`.
2. Lists customer emails currently represented in Escalations or Notifications.
3. Lets the administrator correct sentiment, priority, category, intent, response-required, recognition and escalation.
4. Stores the human-validated example in SQLite.
5. Injects the validated examples into future Qwen prompts immediately.
6. Re-analyzes the selected email through the normal AI pipeline.
7. Optionally rebuilds `pdl-qwen3:8b` from `qwen3:8b` using an Ollama Modelfile and activates the customized model.

This is **supervised example-based optimization**, not gradient/weight fine-tuning. Ollama's supported `ollama create` / Modelfile mechanism is used to create a customized model. True LoRA/QLoRA weight training requires an external fine-tuning framework and an adapter import step.

## Windows Server Network Settings

Administration now exposes:
- Windows network adapter
- Static hostname
- Static IPv4 address
- IPv4 prefix length
- Default gateway
- DNS servers
- NTP servers
- Calculated public API URL

The application applies the settings with PowerShell/Windows networking commands and stores the resulting API URL as `network:api_url`. Monitoring-slot notification links use this URL rather than a fixed localhost URL.

The application default listener is now `0.0.0.0:5000` for Windows Server hosting. The hostname change requires a Windows restart; changing the IP can disconnect the current browser session.

Run the Flask process with the Windows privileges required to modify network configuration.
