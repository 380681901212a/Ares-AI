import json, os
def execute(config: dict, workdir: str) -> str:
    path = config.get("file_path", "")
    if not os.path.isabs(path):
        path = os.path.join(workdir, path)
        
    try:
        import pdfplumber
    except ImportError:
        return "Error: pdfplumber not installed. Please install it."
    try:
        text = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text.append(page.extract_text() or "")
        return "\n".join(text)
    except Exception as e:
        return f"Error reading PDF {path}: {e}"
