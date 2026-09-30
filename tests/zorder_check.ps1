# Does the file dialog actually come to the front.
#
# This is the check that was missing. "The dialog pops behind the browser
# window" was reported twice, and the first fix was shipped on reasoning alone
# because nothing here could see a screen. It did not work. Reasoning about
# window z-order is not evidence about window z-order.
#
# The Windows runner has a real desktop session, so the question can simply be
# asked: put a window in front, run the picker the way the app runs it, and see
# which window Windows says is foremost.
#
# Exits non-zero if the dialog does not come to the front, and prints what did.

$ErrorActionPreference = 'Stop'

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Fg {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Unicode)]
  public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  public delegate bool EnumProc(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc f, IntPtr p);
  public static System.Collections.Generic.List<string> Visible() {
    var outl = new System.Collections.Generic.List<string>();
    EnumWindows(delegate(IntPtr h, IntPtr p) {
      if (!IsWindowVisible(h)) return true;
      var sb = new StringBuilder(512);
      GetWindowText(h, sb, sb.Capacity);
      if (sb.Length > 0) outl.Add(sb.ToString());
      return true;
    }, IntPtr.Zero);
    return outl;
  }
}
"@

function Get-ForegroundTitle {
  $h = [Fg]::GetForegroundWindow()
  if ($h -eq [IntPtr]::Zero) { return '' }
  $sb = New-Object System.Text.StringBuilder 512
  [void][Fg]::GetWindowText($h, $sb, $sb.Capacity)
  return $sb.ToString()
}

# A stand-in for the browser: a maximised window that holds the foreground, so
# the dialog has something real to come out from behind. Without this the test
# would pass against an empty desktop and prove nothing.
$standin = @'
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = "STANDIN WINDOW"
$f.WindowState = "Maximized"
$f.Add_Shown({ $f.Activate() })
[System.Windows.Forms.Application]::Run($f)
'@
Set-Content -Path standin.ps1 -Value $standin -Encoding UTF8

# An earlier step calls the picker and its PowerShell child outlives the
# Python that started it, so its dialog is still on screen. Measuring that one
# is measuring a window opened before this test's stand-in existed, which
# cannot possibly be in front of it. Clear the decks and prove they are clear.
# A dialog is a child window, so neither a process title nor taskkill's
# WINDOWTITLE filter can find it. What can be said for certain is that this
# job's own shell is pwsh, so any Windows PowerShell running here belongs to an
# earlier step and is not wanted. The stand-in below starts after this.
Get-Process powershell -ErrorAction SilentlyContinue |
  Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
if ([Fg]::Visible() | Where-Object { $_ -like '*Choose models to optimise*' }) {
  throw "a file dialog was already open before this test started, so nothing it measures would mean anything"
}
Write-Host "  no leftover dialog on screen"

$sp = Start-Process powershell -ArgumentList '-NoProfile','-STA','-ExecutionPolicy','Bypass','-File','standin.ps1' -PassThru
Start-Sleep -Seconds 4

# Control: the stand-in must really be in front, or the test proves nothing.
$before = Get-ForegroundTitle
Write-Host "  foreground before the picker: '$before'"
if ($before -notlike '*STANDIN*') {
  Stop-Process -Id $sp.Id -Force -ErrorAction SilentlyContinue
  throw "the stand-in window never took the foreground, so this test cannot judge anything"
}

# Now the picker, started the way the app starts it: from a separate process
# that does not own the foreground.
$env:PRISM_PICKER_LOG = (Join-Path (Get-Location) 'picker.log')
Remove-Item $env:PRISM_PICKER_LOG -ErrorAction SilentlyContinue
$pp = Start-Process python -ArgumentList 'tests/pick_once.py' `
  -PassThru -RedirectStandardOutput picker.out -RedirectStandardError picker.err

$seen = ''
$ok = $false
foreach ($i in 1..40) {
  Start-Sleep -Milliseconds 500
  $t = Get-ForegroundTitle
  if ($t) { $seen = $t }
  if ($t -like '*Choose models to optimise*') { $ok = $true; break }
}

Write-Host "  foreground after the picker:  '$seen'"
foreach ($f in 'picker.out','picker.err') {
  if ((Test-Path $f) -and (Get-Item $f).Length -gt 0) {
    Write-Host "  $f :"
    Get-Content $f | ForEach-Object { Write-Host "    $_" }
  }
}
if (Test-Path $env:PRISM_PICKER_LOG) {
  Write-Host "  what the picker script did:"
  Get-Content $env:PRISM_PICKER_LOG | ForEach-Object { Write-Host "    $_" }
} else { Write-Host "  the picker script wrote no log at all" }
Write-Host "  windows on screen at the end:"
foreach ($w in [Fg]::Visible()) { Write-Host "    - $w" }

Stop-Process -Id $pp.Id -Force -ErrorAction SilentlyContinue
Stop-Process -Id $sp.Id -Force -ErrorAction SilentlyContinue
Remove-Item standin.ps1 -ErrorAction SilentlyContinue

if (-not $ok) {
  throw "the file dialog did not come to the front. Foreground was '$seen'"
}
Write-Host "the file dialog came to the front, over a maximised window"
exit 0
