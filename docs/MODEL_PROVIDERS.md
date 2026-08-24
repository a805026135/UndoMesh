# Bring Your Own Model Key

Open the desktop client and select **Model**. Choose OpenAI, DeepSeek, Anthropic, Gemini, Alibaba Model Studio, Kimi/Moonshot AI or Custom; select/edit the model, enter a key, and run **Test connection**. The test makes one minimal inference, which may incur a tiny provider charge.

Kimi 中国区预设使用 `https://api.moonshot.cn/v1`，优先列出 `kimi-k2.7-code` 和 `kimi-k2.7-code-highspeed`；这些名称来自 2026-08-22 对官方 `/models` 接口的实际验证。

Only provider, model and Base URL are saved to `data/config/provider.json`. The GUI API key remains in process memory and is not written to that file, events or audit logs. For CLI use, set the provider-specific environment variable and run:

```powershell
$env:DEEPSEEK_API_KEY="..."
python -m undomesh --provider deepseek --model deepseek-v4-flash --test-connection
```

Custom endpoints must use HTTPS; HTTP is accepted only for localhost. They must implement OpenAI-compatible chat completions and function tool calls. Anthropic and Gemini use their official compatibility layers, so native-only features are unavailable.
