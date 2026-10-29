Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'dispatch|uvicorn|cloudflared' -and $_.Name -match 'python|node|cloudflared' } |
  Select-Object ProcessId, Name, @{n='Cmd';e={ if ($_.CommandLine -and $_.CommandLine.Length -gt 200) { $_.CommandLine.Substring(0,200) } else { $_.CommandLine } }} |
  Format-List

Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -in 8000,8080,3000,5173,8787 } |
  Select-Object LocalAddress, LocalPort, OwningProcess |
  Format-Table -AutoSize