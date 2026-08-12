# agent-status.ps1
# .claude/worktrees/agent-* 配下の .agent-status ファイルを一覧表示する。
# 実行方法: このリポジトリ直下で `powershell -ExecutionPolicy Bypass -File .claude\scripts\agent-status.ps1`
#
# 注意: これは各作業エージェントが最後に自己申告した状態(status/task/current/updated)を表示するだけで、
# 実際にバックグラウンドで生きているかどうかまでは保証しない(エージェントが異常終了すると更新が止まる)。
# 「本当に今動いているか」の確定判定は管理エージェント(Claude Code)側のTaskStopでのみ可能。

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$worktreesDir = Join-Path $root ".claude\worktrees"

if (-not (Test-Path $worktreesDir)) {
    Write-Host "worktreeディレクトリが見つかりません: $worktreesDir"
    exit
}

$rows = @()

Get-ChildItem -Path $worktreesDir -Directory -Filter "agent-*" | ForEach-Object {
    $dir = $_.FullName
    $agentId = $_.Name -replace '^agent-', ''
    $statusFile = Join-Path $dir ".agent-status"

    $status = "(unknown)"
    $task = ""
    $current = ""
    $updated = ""

    if (Test-Path $statusFile) {
        $lines = Get-Content $statusFile -Encoding UTF8
        foreach ($line in $lines) {
            if ($line -match '^\s*status:\s*(.+)$')  { $status  = $matches[1].Trim() }
            if ($line -match '^\s*task:\s*(.+)$')    { $task    = $matches[1].Trim() }
            if ($line -match '^\s*current:\s*(.+)$') { $current = $matches[1].Trim() }
            if ($line -match '^\s*updated:\s*(.+)$') { $updated = $matches[1].Trim() }
        }
    } else {
        $status = "(.agent-statusなし)"
    }

    # 未commitの変更が残っているか(そのworktree内でgit statusを実行)
    $dirty = ""
    try {
        Push-Location $dir
        $gitStatus = git status --porcelain 2>$null
        if ($gitStatus) { $dirty = "変更あり" } else { $dirty = "" }
        Pop-Location
    } catch {
        if ((Get-Location).Path -ne $root) { Pop-Location }
    }

    $rows += [PSCustomObject]@{
        AgentID  = $agentId
        Status   = $status
        Task     = $task
        Current  = $current
        Updated  = $updated
        未commit = $dirty
    }
}

# status: working を先頭に、次いでblocked、それ以外は更新時刻順
$order = @{ "working" = 0; "blocked" = 1 }
$rows = $rows | Sort-Object -Property @{Expression = { if ($order.ContainsKey($_.Status)) { $order[$_.Status] } else { 2 } } }, @{Expression = "Updated"; Descending = $true }

$rows | Format-Table -AutoSize -Wrap

Write-Host ""
Write-Host "件数: $($rows.Count)  (status=workingは $(($rows | Where-Object {$_.Status -eq 'working'}).Count) 件)"
Write-Host "※ これは各エージェントの自己申告(.agent-status)です。本当に稼働中かはClaude Code側のTaskStop確認が最終判定です。"
