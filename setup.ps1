winget install Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements --override "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1"

$env:Path += ";$env:LocalAppData\Programs\Python\Launcher"
$env:Path += ";$env:LocalAppData\Programs\Python\Python312"
$env:Path += ";$env:LocalAppData\Programs\Python\Python312\Scripts"

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*Python\Launcher*") { [Environment]::SetEnvironmentVariable("Path", $userPath + ";$env:LocalAppData\Programs\Python\Launcher", "User") }
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*Python312*") { [Environment]::SetEnvironmentVariable("Path", $userPath + ";$env:LocalAppData\Programs\Python\Python312;$env:LocalAppData\Programs\Python\Python312\Scripts", "User") }

Write-Host "Installing Python Packages..."
py -3.12 -m pip install -r requirements.txt

Write-Host "Starting App..."
py -3.12 app.py
