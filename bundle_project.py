import os
import zipfile
import base64
import html

# --- Configuration ---
PROJECT_NAME = "Options Analyzer Pro"
OUTPUT_HTML = "Options_Analyzer_Pro_Bundle.html"
OUTPUT_ZIP = "Options_Analyzer_Pro_Full.zip"

EXCLUDE_DIRS = ['venv', '__pycache__', '.git', '.gemini', '.pytest_cache']
EXCLUDE_FILES = [OUTPUT_HTML, OUTPUT_ZIP, '.env', 'firebase-credentials.json', 'fyersApi.log', 'fyersRequests.log', 'fyers_auth.log']

INCLUDE_CODE_FILES = [
    'Options_Analyzer_Pro_Standalone.html',
    'app.py', 
    'analysis_engine.py', 
    'strategy_scanner.py', 
    'sector_data.py', 
    'firebase_config.py',
    'templates/dashboard.html',
    'templates/index.html'
]

def create_zip():
    print(f"Creating {OUTPUT_ZIP}...")
    with zipfile.ZipFile(OUTPUT_ZIP, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk('.'):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            
            for file in files:
                if file in EXCLUDE_FILES:
                    continue
                if file.endswith(('.log', '.tmp', '.pyd', '.pyc')):
                    continue
                
                file_path = os.path.join(root, file)
                # Archive path (relative to current dir)
                arc_path = os.path.relpath(file_path, '.')
                zipf.write(file_path, arc_path)
    print(f"ZIP created: {os.path.abspath(OUTPUT_ZIP)}")

def generate_html():
    print(f"Generating {OUTPUT_HTML}...")
    
    # Read ZIP and encode to base64
    with open(OUTPUT_ZIP, "rb") as f:
        zip_base64 = base64.b64encode(f.read()).decode('utf-8')

    # Read code for main files
    code_sections = ""
    for file_name in INCLUDE_CODE_FILES:
        if os.path.exists(file_name):
            with open(file_name, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
                code_sections += f"""
                <div class="code-container">
                    <div class="code-header">{file_name}</div>
                    <pre><code>{html.escape(content)}</code></pre>
                </div>"""

    html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{PROJECT_NAME} - Portable Bundle</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&family=Space+Mono&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #0b1220;
            --card: #162035;
            --accent: #00d4ff;
            --text: #dce9ff;
            --text-dim: #6b88a8;
            --border: rgba(0, 212, 255, 0.15);
        }}
        body {{
            background: var(--bg);
            color: var(--text);
            font-family: 'Inter', sans-serif;
            margin: 0;
            padding: 40px 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }}
        .header {{
            text-align: center;
            margin-bottom: 40px;
            max-width: 800px;
        }}
        h1 {{ font-weight: 800; font-size: 2.5rem; margin: 0; color: var(--accent); }}
        p {{ color: var(--text-dim); margin-top: 10px; }}
        
        .download-box {{
            background: var(--card);
            border: 1px solid var(--accent);
            padding: 30px;
            border-radius: 16px;
            box-shadow: 0 0 30px rgba(0, 212, 255, 0.2);
            text-align: center;
            margin-bottom: 50px;
            width: 100%;
            max-width: 400px;
        }}
        .btn {{
            background: var(--accent);
            color: #000;
            border: none;
            padding: 15px 30px;
            font-size: 1rem;
            font-weight: 800;
            border-radius: 8px;
            cursor: pointer;
            text-decoration: none;
            display: inline-block;
            transition: transform 0.2s, box-shadow 0.2s;
        }}
        .btn:hover {{
            transform: scale(1.05);
            box-shadow: 0 0 20px rgba(0, 212, 255, 0.5);
        }}
        
        .code-viewer {{
            width: 100%;
            max-width: 1000px;
        }}
        .code-container {{
            background: #050912;
            border: 1px solid var(--border);
            border-radius: 12px;
            margin-bottom: 30px;
            overflow: hidden;
        }}
        .code-header {{
            background: var(--card);
            padding: 10px 20px;
            font-family: 'Space Mono', monospace;
            font-size: 0.8rem;
            font-weight: 600;
            color: var(--accent);
            border-bottom: 1px solid var(--border);
        }}
        pre {{
            margin: 0;
            padding: 20px;
            overflow-x: auto;
            font-family: 'Space Mono', monospace;
            font-size: 0.85rem;
            line-height: 1.5;
            color: #a9b7c6;
        }}
        
        ::-webkit-scrollbar {{ width: 8px; height: 8px; }}
        ::-webkit-scrollbar-track {{ background: var(--bg); }}
        ::-webkit-scrollbar-thumb {{ background: var(--card); border-radius: 4px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: #1e2a44; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{PROJECT_NAME}</h1>
        <p>Complete source code bundle for the Options Analyzer Pro. All logic, styles, and templates are contained within this file and the attached archive.</p>
    </div>

    <div class="download-box">
        <h3 style="margin-top:0">📦 Project Archive</h3>
        <p style="font-size: 0.85rem; margin-bottom: 20px;">Download the full project structure including static assets, templates, and Python sources in a single ZIP file.</p>
        <a href="data:application/zip;base64,{zip_base64}" download="{OUTPUT_ZIP}" class="btn">Download Project ZIP</a>
    </div>

    <div class="code-viewer">
        <h2 style="text-align:center; color: var(--text-dim); text-transform: uppercase; font-size: 0.8rem; letter-spacing: 2px;">Source Code Reference</h2>
        {code_sections}
    </div>

    <script>
        console.log("{PROJECT_NAME} Bundle Loaded");
    </script>
</body>
</html>
"""

    with open(OUTPUT_HTML, "w", encoding='utf-8') as f:
        f.write(html_template)
    print(f"HTML created: {os.path.abspath(OUTPUT_HTML)}")

if __name__ == "__main__":
    create_zip()
    generate_html()
    print("\\nAll done! You can now share the HTML file.")
