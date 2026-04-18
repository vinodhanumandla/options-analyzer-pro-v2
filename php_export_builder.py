import os
import shutil
import zipfile

# --- Configuration ---
PROJECT_NAME = "Options Analyzer Pro"
OUTPUT_ZIP = "OptionsAnalyzer_PHP_Version.zip"
TEMP_DIR = "php_export_temp"

EXCLUDE_DIRS = ['venv', '__pycache__', '.git', '.gemini', '.pytest_cache', TEMP_DIR]
EXCLUDE_FILES = [OUTPUT_ZIP, '.env', 'OptionsAnalyzerPro_GitHub.zip', 'Options_Analyzer_Pro_Full.zip', 'fyersApi.log', 'fyersRequests.log', 'php_export_builder.py', 'flask_error_log.txt']

INDEX_PHP_CONTENT = """<?php
/**
 * Options Analyzer Pro - PHP Launcher
 * 
 * This script ensures the Python Flask application is running in the background,
 * and then automatically redirects the user to the application's port.
 */

$flask_port = 5001;
$host = '127.0.0.1';

// Check if Python Flask app is running
$connection = @fsockopen($host, $flask_port, $errno, $errstr, 1);

if (!is_resource($connection)) {
    // Port is closed, start the Python app
    $os_name = strtoupper(substr(PHP_OS, 0, 3));
    
    if ($os_name === 'WIN') {
        // Windows: use start /B to run in background
        $cmd = 'start /B python app.py';
    } else {
        // Linux/Mac: use nohup or & to run in background
        $cmd = 'nohup python3 app.py > /dev/null 2>&1 &';
    }
    
    // Execute the command without waiting for it to finish
    pclose(popen($cmd, "r"));
    
    // Wait a few seconds for Flask to initialize
    sleep(4);
} else {
    // Port is already open, app is running!
    fclose($connection);
}

// Determine the correct protocol and redirect the user
$protocol = isset($_SERVER['HTTPS']) && $_SERVER['HTTPS'] === 'on' ? "https" : "http";
$domain = $_SERVER['SERVER_NAME'];

// If running on localhost, redirect to localhost:5001
// If running on a domain, redirect to domain:5001
$redirect_url = $protocol . "://" . $domain . ":" . $flask_port;

header("Location: " . $redirect_url);
exit();
?>"""

def build_php_version():
    print(f"Creating temporary directory: {TEMP_DIR}")
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    os.makedirs(TEMP_DIR)
    
    # 1. Copy all relevant files to the temp directory
    print("Copying project files...")
    for item in os.listdir('.'):
        if item in EXCLUDE_DIRS or item in EXCLUDE_FILES or item.endswith('.zip') or item.endswith('.log'):
            continue
            
        src_path = os.path.join('.', item)
        dst_path = os.path.join(TEMP_DIR, item)
        
        if os.path.isdir(src_path):
            shutil.copytree(src_path, dst_path, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*EXCLUDE_DIRS, *EXCLUDE_FILES, '*.log', '*.pyc', '*.tmp'))
        else:
            shutil.copy2(src_path, dst_path)

    # 2. Inject the index.php script
    print("Injecting index.php launcher...")
    with open(os.path.join(TEMP_DIR, 'index.php'), 'w', encoding='utf-8') as f:
        f.write(INDEX_PHP_CONTENT)
        
    # 3. Zip the directory
    print(f"Zipping into {OUTPUT_ZIP}...")
    with zipfile.ZipFile(OUTPUT_ZIP, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(TEMP_DIR):
            # Prune excluded directories (mostly a safety catch, they shouldn't be here)
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            
            for file in files:
                file_path = os.path.join(root, file)
                # Archive path needs to be relative to TEMP_DIR so it extracts cleanly
                arc_path = os.path.relpath(file_path, TEMP_DIR)
                zipf.write(file_path, arc_path)

    # 4. Cleanup
    print("Cleaning up temporary files...")
    shutil.rmtree(TEMP_DIR)
    
    print(f"Successfully created: {os.path.abspath(OUTPUT_ZIP)}")

if __name__ == "__main__":
    build_php_version()
