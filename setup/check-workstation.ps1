$ErrorActionPreference = 'SilentlyContinue'
$targets = @(
  @{ Name = 'Ollama'; Url = 'http://127.0.0.1:11434/api/tags' },
  @{ Name = 'ComfyUI'; Url = 'http://127.0.0.1:8188/system_stats' },
  @{ Name = 'StoryFrame'; Url = 'http://127.0.0.1:8000/api/health' }
)
foreach ($target in $targets) {
  try {
    $result = Invoke-RestMethod -Uri $target.Url -TimeoutSec 5
    Write-Host "[OK] $($target.Name) - $($target.Url)" -ForegroundColor Green
    if ($target.Name -eq 'StoryFrame') { $result | ConvertTo-Json -Depth 6 }
  } catch {
    Write-Host "[FAIL] $($target.Name) - $($target.Url)" -ForegroundColor Red
  }
}
