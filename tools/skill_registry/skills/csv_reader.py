import json, os
def execute(config: dict, workdir: str) -> str:
    path = config.get("file_path", "")
    if not os.path.isabs(path):
        path = os.path.join(workdir, path)
        
    try:
        import pandas as pd
    except ImportError:
        return "Error: pandas not installed. Please install it."
    try:
        df = pd.read_csv(path)
        return df.head(250).to_string()
    except Exception as e:
        return f"Error reading CSV {path}: {e}"
