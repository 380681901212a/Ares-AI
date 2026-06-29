import os, requests
def notify_telegram(message: str, file_path: str = None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"}, timeout=10)
        
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                             data={"chat_id": chat_id}, files={"document": f}, timeout=30)
    except Exception as e:
        print(f"Telegram notify error: {e}")
