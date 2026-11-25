[CmdletBinding()]
param(
    [Parameter(Mandatory,Position=0)]
    [ValidateSet('Install','Uninstall','Status','Start','Check','Backup','Configure','Run','RunProbe','Probe')]
    [string]$Action,
    [string]$Python,
    [string]$DataRoot = (Join-Path ([Environment]::GetFolderPath('UserProfile')) '.epl-prediction-agent'),
    [string]$StateDir = (Join-Path $env:LOCALAPPDATA 'EPLPredictionAgent'),
    [string]$TaskName = 'EPL Prediction Collector',
    [string]$Repository,
    [string]$ExpectedSid,
    [int]$Issue,
    [string]$PagesCodeBranch = 'epl-v1',
    [string]$BackupBase,
    [switch]$AtLogon,
    [switch]$UseGitCredential,
    [ValidateSet('ODDS_API_KEY','EPL_GITHUB_TOKEN','EPL_SNAPSHOT_GITHUB_TOKEN','EPL_AUDIT_GITHUB_TOKEN')]
    [string]$SecretName,
    [pscredential]$Credential
)
$ErrorActionPreference = 'Stop'
$owner = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($ExpectedSid -and $ExpectedSid -ne $owner.User.Value) { throw 'Installation requires the selected collector identity.' }
$managed = 'EPL prediction agent / managed collector / '
$scriptFile = $PSCommandPath
$runtime = Join-Path $PSScriptRoot 'collector_runtime.py'
$configFile = Join-Path $StateDir 'collector.json'
$probeConfig = Join-Path $StateDir 'collector-probe.json'
$proofFile = Join-Path $StateDir 'scheduler-probe.json'
$taskMarker = $managed + [IO.Path]::GetFullPath($StateDir)
if ($TaskName -notmatch '^[A-Za-z0-9_. -]{1,90}$') { throw 'Invalid collector task name.' }
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) ($TaskName+'.lnk')

function Find-OwnedShortcut {
    if (!(Test-Path -LiteralPath $shortcutPath)) { return }
    $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    if ($link.Description -ne $taskMarker) { throw 'The existing Startup shortcut has a different owner.' }
    return $link
}

function Protect-Directory([string]$Path) {
    $known = @('collector.json','collector-probe.json','scheduler-probe.json','ODDS_API_KEY.dpapi',
        'EPL_GITHUB_TOKEN.dpapi','EPL_SNAPSHOT_GITHUB_TOKEN.dpapi','EPL_AUDIT_GITHUB_TOKEN.dpapi')
    if (Test-Path -LiteralPath $Path) {
        if (Get-ChildItem -LiteralPath $Path -Force | Where-Object { $_.Name -notin $known -or $_.PSIsContainer }) {
            throw 'Choose a private settings directory dedicated to this collector.'
        }
    }
    $item = [IO.Directory]::CreateDirectory($Path)
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Private settings directory cannot be a reparse point.' }
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true,$false)
    foreach ($sid in @($owner.User.Value,'S-1-5-18','S-1-5-32-544') | Select-Object -Unique) {
        $id = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($id,'FullControl','ContainerInherit,ObjectInherit','None','Allow')
        $acl.AddAccessRule($rule)
    }
    # Apply only the changed access rules; setting the audit ACL requires an unrelated privilege.
    if ($PSVersionTable.PSEdition -eq 'Core') {
        [IO.FileSystemAclExtensions]::SetAccessControl($item,$acl)
    } else {
        $item.SetAccessControl($acl)
    }
}

function Find-OwnedTask([string]$Name) {
    $task = Get-ScheduledTask -TaskPath '\' -TaskName $Name -ErrorAction SilentlyContinue
    if ($task -and $task.Description -ne $taskMarker) {
        throw "Another application owns the scheduled task named '$Name'."
    }
    return $task
}

function Set-PrivateEnvironment {
    foreach ($key in @('ODDS_API_KEY','EPL_GITHUB_TOKEN','EPL_SNAPSHOT_GITHUB_TOKEN','EPL_AUDIT_GITHUB_TOKEN')) {
        $file = Join-Path $StateDir ($key+'.dpapi')
        if (Test-Path -LiteralPath $file -PathType Leaf) {
            $secure = Get-Content -LiteralPath $file -Raw | ConvertTo-SecureString
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try {
                [Environment]::SetEnvironmentVariable($key,[Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr),'Process')
            } finally {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
                $secure.Dispose()
            }
        }
    }
}

function Register-CollectorTask([string]$Name, [string]$Mode, $Trigger) {
    if ($scriptFile -match '["\r\n]' -or $StateDir -match '["\r\n]') { throw 'Unsupported task path.' }
    $shell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $launch = New-ScheduledTaskAction -Execute $shell -Argument (
        '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "'+$scriptFile+'" -Action '+$Mode+' -StateDir "'+$StateDir+'"')
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([timespan]::Zero) -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 8 -MultipleInstances IgnoreNew
    $params = @{TaskPath='\';TaskName=$Name;Action=$launch;Trigger=$Trigger;Settings=$settings;Description=$taskMarker;Force=$true}
    if ($Credential) {
        $login = New-Object Security.Principal.NTAccount($Credential.UserName)
        if ($login.Translate([Security.Principal.SecurityIdentifier]).Value -ne $owner.User.Value) {
            throw 'Use the same Windows account that owns the collector and its credentials.'
        }
        # The task registration API stores the logon credential in Windows, never in action arguments.
        Register-ScheduledTask @params -User $Credential.UserName -Password $Credential.GetNetworkCredential().Password -ErrorAction Stop | Out-Null
    } else {
        $principal = New-ScheduledTaskPrincipal -UserId $owner.User.Value -LogonType S4U -RunLevel Limited
        Register-ScheduledTask @params -Principal $principal -ErrorAction Stop | Out-Null
    }
}

if ($Action -eq 'Configure') {
    if (-not $SecretName) { throw 'Specify -SecretName. A hidden prompt will request its value.' }
    Protect-Directory $StateDir
    $secret = Read-Host "Enter $SecretName" -AsSecureString
    if ($secret.Length -eq 0) { throw 'No value supplied.' }
    try { $secret | ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $StateDir ($SecretName+'.dpapi')) -Encoding ASCII }
    finally { $secret.Dispose() }
    Write-Output "Saved $SecretName for this Windows identity."
    return
}
if ($Action -eq 'Status') {
    $task = Find-OwnedTask $TaskName
    $info = if ($task) { $task | Get-ScheduledTaskInfo }
    [pscustomobject]@{installed=[bool]$task;logon_fallback=[bool](Find-OwnedShortcut);state=$task.State;last_run=$info.LastRunTime;last_result=$info.LastTaskResult;
        identity=$task.Principal.UserId;logon=$task.Principal.LogonType;settings_present=(Test-Path -LiteralPath $configFile);
        scheduler_probe=if(Test-Path -LiteralPath $proofFile){Get-Content -Raw -LiteralPath $proofFile | ConvertFrom-Json}else{$null}}
    return
}
if ($Action -eq 'Uninstall') {
    if (Find-OwnedTask $TaskName) { Unregister-ScheduledTask -TaskPath '\' -TaskName $TaskName -Confirm:$false }
    if (Find-OwnedShortcut) { Remove-Item -LiteralPath $shortcutPath }
    return
}
if ($Action -eq 'Start') {
    $installed = Find-OwnedTask $TaskName
    if (-not $installed -and -not (Find-OwnedShortcut)) { throw 'Install the collector task first.' }
    $legacy = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -match '(?i)(^|\s)epl_agent\s+worker(\s|$)' }
    if ($legacy) { throw 'An EPL worker is already running. Complete the approved handover before starting the task.' }
    if ($installed) { Start-ScheduledTask -TaskPath '\' -TaskName $TaskName -ErrorAction Stop }
    else { Start-Process -FilePath $shortcutPath -WindowStyle Hidden }
    return
}

if ($Action -in @('Install','Probe')) {
    # Refuse unrelated tasks before writing any settings or scheduling work.
    $owned = Find-OwnedTask $TaskName
    if ($Action -eq 'Install' -and $owned -and $owned.State -eq 'Running') { throw 'Stop the managed task before updating its startup configuration.' }
    if ($Action -eq 'Probe' -and (Find-OwnedTask ($TaskName+'.Probe'))) { throw 'A scheduled identity probe is already present.' }
}
if ($Action -in @('Run','RunProbe') -or ((Test-Path -LiteralPath $configFile) -and -not $Python -and -not $Repository)) {
    $readConfig = if ($Action -eq 'RunProbe') { $probeConfig } else { $configFile }
    $conf = Get-Content -LiteralPath $readConfig -Raw | ConvertFrom-Json
    $Python=$conf.python; $DataRoot=$conf.data_root; $Repository=$conf.repo; $Issue=$conf.issue
    $PagesCodeBranch=$conf.pages_branch; $UseGitCredential=[bool]$conf.git_credential
}
if (-not $Python) { $Python = (Get-Command python.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source }
$Python = (Resolve-Path -LiteralPath $Python).Path
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
if ($Action -eq 'Backup') {
    $arguments = @($runtime,'backup','--root',$DataRoot)
    if ($BackupBase) { $arguments += @('--backup-base',$BackupBase) }
    & $Python @arguments
    if ($LASTEXITCODE) { throw 'SQLite backup verification failed.' }
    return
}
if ($Repository -notmatch '^[A-Za-z0-9-]+/[A-Za-z0-9_.-]+$' -or $Issue -le 0) { throw 'Specify a public -Repository owner/name and a positive -Issue.' }
if ($PagesCodeBranch -and $PagesCodeBranch -ne 'epl-v1') { throw 'Unsupported Pages code branch.' }
if ($Action -in @('Install','Probe')) {
    Protect-Directory $StateDir
    $conf = @{python=$Python;data_root=$DataRoot;repo=$Repository;issue=$Issue;pages_branch=$PagesCodeBranch;git_credential=[bool]$UseGitCredential}
    if ($Action -eq 'Install') {
        $oldShortcut = Find-OwnedShortcut
        if ($AtLogon) {
            if ($owned) { throw 'A boot task already exists. Use it or uninstall it before selecting logon startup.' }
            $link = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
            $link.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
            $link.Arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "'+$scriptFile+'" -Action Run -StateDir "'+$StateDir+'"'
            $link.Description = $taskMarker
            $link.Save()
        } else {
            $preflight = @{Python=$Python;DataRoot=$DataRoot;StateDir=$StateDir;Repository=$Repository;
                Issue=$Issue;TaskName=$TaskName;UseGitCredential=$UseGitCredential;PagesCodeBranch=$PagesCodeBranch}
            if ($Credential) { $preflight['Credential']=$Credential }
            & $scriptFile Probe @preflight | Out-Null
            Register-CollectorTask $TaskName 'Run' (New-ScheduledTaskTrigger -AtStartup)
            if ($oldShortcut) { Remove-Item -LiteralPath $shortcutPath }
        }
        $conf | ConvertTo-Json | Set-Content -LiteralPath $configFile -Encoding UTF8
        Write-Output $(if ($AtLogon) {'Current-user sign-in shortcut installed.'} else {'Boot task installed.'})
    } else {
        $name = $TaskName+'.Probe'
        $start = [datetime]::UtcNow
        $conf | ConvertTo-Json | Set-Content -LiteralPath $probeConfig -Encoding UTF8
        try {
            Register-CollectorTask $name 'RunProbe' (New-ScheduledTaskTrigger -Once -At ((Get-Date).AddDays(1)))
            Start-ScheduledTask -TaskPath '\' -TaskName $name -ErrorAction Stop
            for ($i=0; $i -lt 75; $i++) {
                Start-Sleep -Seconds 1
                if (Test-Path -LiteralPath $proofFile) {
                    $proof = Get-Content -LiteralPath $proofFile -Raw | ConvertFrom-Json
                    if ([datetimeoffset]::Parse($proof.at).UtcDateTime -ge $start) { break }
                }
            }
            if (-not $proof -or [datetimeoffset]::Parse($proof.at).UtcDateTime -lt $start -or -not $proof.ok) {
                throw 'Boot identity probe failed. Check Task Scheduler, credentials and outbound HTTPS under that identity.'
            }
            $proof
        } finally {
            if (Find-OwnedTask $name) { Unregister-ScheduledTask -TaskPath '\' -TaskName $name -Confirm:$false }
            Remove-Item -LiteralPath $probeConfig -ErrorAction SilentlyContinue
        }
    }
    return
}
$env:EPL_EVIDENCE_REPO=$Repository; $env:EPL_EVIDENCE_ISSUE="$Issue"; $env:EPL_PAGES_CODE_BRANCH=$PagesCodeBranch
$env:EPL_AGENT_HOME=$DataRoot; $env:PYTHONUTF8='1'; $env:PYTHONUNBUFFERED='1'
$mode = if ($Action -eq 'Run') {'run'} else {'check'}
$arguments = @($runtime,$mode,'--root',$DataRoot)
if ($UseGitCredential) { $arguments += '--git-credential' }
$ok = $false
try {
    Set-PrivateEnvironment
    & $Python @arguments
    if ($LASTEXITCODE) { throw 'Collector process returned a failure.' }
    $ok = $true
} finally {
    if ($Action -eq 'RunProbe') {
        @{ok=$ok;at=[datetimeoffset]::UtcNow.ToString('o');identity=$owner.Name} |
            ConvertTo-Json | Set-Content -LiteralPath $proofFile -Encoding UTF8
    }
}
