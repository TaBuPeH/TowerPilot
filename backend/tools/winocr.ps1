param([string]$img, [switch]$Serve)
# Windows built-in OCR (WinRT) - run with Windows PowerShell 5.1, NOT pwsh.
# Output: one line per recognized text line: "<y>`t<x>`t<text>"
$null = [Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics,ContentType=WindowsRuntime]
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$awaitM = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
function Await($op, $type) {
  $t = $awaitM.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(); $t.Result
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
function Read-Image([string]$path) {
  $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
  $stream = Await ($file.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
  try {
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  } finally { $stream.Dispose() }
  $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
  foreach ($line in $result.Lines) {
    $x = 999999; $y = 999999
    foreach ($w in $line.Words) {
      $r = $w.BoundingRect
      if ($r.X -lt $x) { $x = [int]$r.X }
      if ($r.Y -lt $y) { $y = [int]$r.Y }
    }
    "{0}`t{1}`t{2}" -f $y, $x, $line.Text
  }
}
if (-not $Serve) { Read-Image $img; return }
$ErrorActionPreference = 'Stop'
# Serve: one JSON request per stdin line, {"id": n, "images": [{"index": i, "path": p}]}.
# Every reply is framed by its request id so a caller can discard a late reply.
$out = [Console]::Out
while ($null -ne ($request = [Console]::In.ReadLine())) {
  $id = 'unparsed'
  try {
    $job = $request | ConvertFrom-Json
    $id = [string]$job.id
    $lines = New-Object System.Collections.Generic.List[string]
    foreach ($entry in $job.images) {
      $lines.Add('TP_OCR_IMAGE' + [char]9 + $entry.index)
      foreach ($row in @(Read-Image $entry.path)) { $lines.Add($row) }
    }
    $out.WriteLine('TP_OCR_BEGIN' + [char]9 + $id)
    foreach ($row in $lines) { $out.WriteLine($row) }
    $out.WriteLine('TP_OCR_DONE' + [char]9 + $id)
  } catch {
    $out.WriteLine('TP_OCR_ERROR' + [char]9 + $id + [char]9 + ($_.Exception.Message -replace '\s+', ' '))
  }
  $out.Flush()
}
