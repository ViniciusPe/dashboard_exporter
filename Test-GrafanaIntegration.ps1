<#
.SYNOPSIS
    Valida o fluxo ServiceNow -> Grafana (criação de time e inserção de usuário).

.DESCRIPTION
    Executa em sequência todos os cenários do documento ServiceNow-Grafana-Integration.md,
    cobrindo idempotência, 404 controlado, race-condition, autenticação inválida e cleanup.

    Pré-requisitos:
      - PowerShell 5.1+ (Windows nativo) ou PowerShell 7+
      - Conectividade HTTPS até $Config.BaseUrl
      - Token com role Admin no Grafana

    Como rodar:
      1) Edite .\config.ps1 (URL, token, times, e-mails)
      2) Mantenha $Config.DryRun = $true na 1ª execução  -> apenas GETs (não muda nada)
      3) Se OK, mude DryRun = $false e rode de novo     -> executa POSTs
      4) Ative DoCleanup = $true para apagar o time 'Teste' criado

    Saída:
      - Log colorido no console
      - Relatório resumido (OK/FAIL) ao final
      - Arquivo .\log\run-YYYYMMDD-HHmmss.log com cada request/response
#>

# ============================================================
# Bootstrap
# ============================================================
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'config.ps1')

# TLS 1.2 obrigatório (Windows PowerShell 5.1 usa SSL3/TLS1.0 por padrão)
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

$LogDir = Join-Path $ScriptDir 'log'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("run-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))

$Results = New-Object System.Collections.Generic.List[object]

# ============================================================
# Helpers
# ============================================================
function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'HH:mm:ss'
    $line = "[$ts] [$Level] $Message"
    $color = switch ($Level) {
        'OK'    { 'Green' }
        'SKIP'  { 'Yellow' }
        'FAIL'  { 'Red' }
        'STEP'  { 'Cyan' }
        'DEBUG' { 'DarkGray' }
        default { 'Gray' }
    }
    Write-Host $line -ForegroundColor $color
    Add-Content -Path $LogFile -Value $line
}

function Invoke-Grafana {
    <#
      Wrapper único para todas as chamadas. Retorna PSObject:
        .StatusCode  (int)
        .Body        (objeto parseado ou string)
        .RawError    (mensagem ou $null)
    #>
    param(
        [Parameter(Mandatory)] [ValidateSet('GET','POST','DELETE','PUT')] [string]$Method,
        [Parameter(Mandatory)] [string]$Path,
        [object]$BodyObj = $null,
        [string]$OverrideToken = $null,
        [int]$Retry = 0
    )

    $token = if ($OverrideToken) { $OverrideToken } else { $Config.Token }
    $url   = "$($Config.BaseUrl.TrimEnd('/'))$Path"
    $headers = @{
        'Authorization' = "Bearer $token"
        'Accept'        = 'application/json'
    }
    if ($BodyObj) { $headers['Content-Type'] = 'application/json' }

    $bodyJson = if ($BodyObj) { $BodyObj | ConvertTo-Json -Depth 10 -Compress } else { $null }

    Write-Log "$Method $url $(if($bodyJson){"body=$bodyJson"})" 'DEBUG'

    try {
        $params = @{
            Method      = $Method
            Uri         = $url
            Headers     = $headers
            TimeoutSec  = $Config.TimeoutSec
            ErrorAction = 'Stop'
            UseBasicParsing = $true
        }
        if ($bodyJson) { $params.Body = $bodyJson }

        $resp = Invoke-WebRequest @params
        $parsed = $null
        if ($resp.Content) {
            try { $parsed = $resp.Content | ConvertFrom-Json -ErrorAction Stop } catch { $parsed = $resp.Content }
        }
        return [pscustomobject]@{
            StatusCode = [int]$resp.StatusCode
            Body       = $parsed
            RawError   = $null
        }
    }
    catch [System.Net.WebException] {
        $status = 0
        $bodyText = $_.Exception.Message
        if ($_.Exception.Response) {
            $status = [int]$_.Exception.Response.StatusCode
            try {
                $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())
                $bodyText = $sr.ReadToEnd()
                $sr.Close()
            } catch {}
        }

        # Retry com backoff exponencial para 5xx/429
        if (($status -ge 500 -or $status -eq 429) -and $Retry -lt $Config.MaxRetries) {
            $wait = [Math]::Pow(2, $Retry)
            Write-Log "HTTP $status - retry em ${wait}s (tentativa $($Retry+1)/$($Config.MaxRetries))" 'WARN'
            Start-Sleep -Seconds $wait
            return Invoke-Grafana -Method $Method -Path $Path -BodyObj $BodyObj -OverrideToken $OverrideToken -Retry ($Retry+1)
        }

        $parsed = $bodyText
        try { $parsed = $bodyText | ConvertFrom-Json -ErrorAction Stop } catch {}
        return [pscustomobject]@{
            StatusCode = $status
            Body       = $parsed
            RawError   = $bodyText
        }
    }
    catch {
        return [pscustomobject]@{
            StatusCode = 0
            Body       = $null
            RawError   = $_.Exception.Message
        }
    }
}

function Add-Result {
    param([string]$TestId, [string]$Description, [string]$Status, [string]$Detail = '')
    $Results.Add([pscustomobject]@{
        Test = $TestId; Description = $Description; Status = $Status; Detail = $Detail
    })
    Write-Log ("$TestId - $Description -> $Status $(if($Detail){"| $Detail"})") $Status
}

# Funções que espelham o pseudocódigo do documento
function Get-TeamExact {
    param([string]$Name)
    $r = Invoke-Grafana -Method GET -Path "/api/teams/search?name=$([uri]::EscapeDataString($Name))"
    if ($r.StatusCode -ne 200) { return @{ Found=$false; Error=$r } }
    $teams = if ($r.Body.PSObject.Properties.Name -contains 'teams') { $r.Body.teams } else { @() }
    $match = $teams | Where-Object { $_.name -ceq $Name } | Select-Object -First 1
    return @{ Found = [bool]$match; Team = $match; Raw = $r }
}

function New-Team {
    param([string]$Name)
    Invoke-Grafana -Method POST -Path '/api/teams' -BodyObj @{
        name = $Name; email = ''; orgId = $Config.OrgId
    }
}

function Get-UserByEmail {
    param([string]$Email)
    Invoke-Grafana -Method GET -Path "/api/users/lookup?loginOrEmail=$([uri]::EscapeDataString($Email))"
}

function Get-TeamMembers {
    param([int]$TeamId)
    Invoke-Grafana -Method GET -Path "/api/teams/$TeamId/members"
}

function Add-TeamMember {
    param([int]$TeamId, [int]$UserId)
    Invoke-Grafana -Method POST -Path "/api/teams/$TeamId/members" -BodyObj @{ userId = $UserId }
}

function Remove-Team {
    param([int]$TeamId)
    Invoke-Grafana -Method DELETE -Path "/api/teams/$TeamId"
}

# ============================================================
# Banner
# ============================================================
Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ' Validação ServiceNow -> Grafana (testes de integração)' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
Write-Log "BaseUrl       : $($Config.BaseUrl)" 'STEP'
Write-Log "OrgId         : $($Config.OrgId)" 'STEP'
Write-Log "TeamExistente : $($Config.TeamExistente)" 'STEP'
Write-Log "TeamNovo      : $($Config.TeamNovo)" 'STEP'
Write-Log "User1         : $($Config.User1)" 'STEP'
Write-Log "User2         : $($Config.User2)" 'STEP'
Write-Log "DryRun        : $($Config.DryRun)" 'STEP'
Write-Log "DoCleanup     : $($Config.DoCleanup)" 'STEP'
Write-Log "LogFile       : $LogFile" 'STEP'
Write-Host ''

# ============================================================
# T1 - Conectividade + autenticação
# ============================================================
Write-Log '--- T1: Conectividade + auth (GET /api/org) ---' 'STEP'
$r = Invoke-Grafana -Method GET -Path '/api/org'
if ($r.StatusCode -eq 200) {
    Add-Result 'T1' 'Auth + conectividade' 'OK' "Org=$($r.Body.name) id=$($r.Body.id)"
} else {
    Add-Result 'T1' 'Auth + conectividade' 'FAIL' "HTTP $($r.StatusCode) - $($r.RawError)"
    Write-Log 'Abortando: sem auth/conectividade não dá pra seguir.' 'FAIL'
    return
}

# ============================================================
# T2 - Time que JÁ EXISTE: deve dar SKIP
# ============================================================
Write-Log "--- T2: Time existente '$($Config.TeamExistente)' deve dar SKIP ---" 'STEP'
$lookup = Get-TeamExact -Name $Config.TeamExistente
if ($lookup.Found) {
    Add-Result 'T2' "Time '$($Config.TeamExistente)' já existe" 'SKIP' "id=$($lookup.Team.id) memberCount=$($lookup.Team.memberCount)"
    $script:TeamExistenteId = [int]$lookup.Team.id
} else {
    Add-Result 'T2' "Time '$($Config.TeamExistente)' deveria existir" 'FAIL' 'Não encontrado - confira o nome'
}

# ============================================================
# T3 - Time NOVO: criar (ou SKIP se rodou antes)
# ============================================================
Write-Log "--- T3: Criar time novo '$($Config.TeamNovo)' ---" 'STEP'
$script:TeamNovoId = $null
$lookup = Get-TeamExact -Name $Config.TeamNovo

if ($lookup.Found) {
    Add-Result 'T3' "Time '$($Config.TeamNovo)' já existia" 'SKIP' "id=$($lookup.Team.id) (provavelmente run anterior)"
    $script:TeamNovoId = [int]$lookup.Team.id
}
elseif ($Config.DryRun) {
    Add-Result 'T3' "Criar '$($Config.TeamNovo)'" 'SKIP' 'DryRun=true - POST não executado'
}
else {
    $create = New-Team -Name $Config.TeamNovo
    switch ($create.StatusCode) {
        200 {
            $script:TeamNovoId = [int]$create.Body.teamId
            Add-Result 'T3' "Time '$($Config.TeamNovo)' criado" 'OK' "id=$($script:TeamNovoId)"
        }
        409 {
            # race condition: alguém criou - relookup
            $relook = Get-TeamExact -Name $Config.TeamNovo
            if ($relook.Found) {
                $script:TeamNovoId = [int]$relook.Team.id
                Add-Result 'T3' 'Race-condition resolvida' 'OK' "id=$($script:TeamNovoId)"
            } else {
                Add-Result 'T3' 'POST 409 mas team não encontrado' 'FAIL' $create.RawError
            }
        }
        default {
            Add-Result 'T3' "Criar '$($Config.TeamNovo)'" 'FAIL' "HTTP $($create.StatusCode) - $($create.RawError)"
        }
    }
}

# ============================================================
# T4 - Idempotência: tentar criar o mesmo time de novo deve dar SKIP
# ============================================================
Write-Log '--- T4: Idempotência - re-tentar criar mesmo time ---' 'STEP'
if (-not $script:TeamNovoId) {
    Add-Result 'T4' 'Idempotência' 'SKIP' 'T3 não produziu teamId (DryRun ou falha)'
} else {
    $lookup2 = Get-TeamExact -Name $Config.TeamNovo
    if ($lookup2.Found -and [int]$lookup2.Team.id -eq $script:TeamNovoId) {
        Add-Result 'T4' 'Idempotência confirmada (lookup acharia antes do POST)' 'OK' "id=$($script:TeamNovoId)"
    } else {
        Add-Result 'T4' 'Idempotência' 'FAIL' 'Lookup não confirmou existência'
    }
}

# ============================================================
# T5 - Lookup de usuário válido
# ============================================================
Write-Log "--- T5: Lookup usuário '$($Config.User1)' ---" 'STEP'
$u1 = Get-UserByEmail -Email $Config.User1
if ($u1.StatusCode -eq 200) {
    $script:User1Id = [int]$u1.Body.id
    Add-Result 'T5' "Lookup '$($Config.User1)'" 'OK' "userId=$($script:User1Id) login=$($u1.Body.login)"
} else {
    Add-Result 'T5' "Lookup '$($Config.User1)'" 'FAIL' "HTTP $($u1.StatusCode) - $($u1.RawError)"
}

# ============================================================
# T6 - Lookup de usuário inexistente (simula quem nunca logou via SSO)
# ============================================================
Write-Log '--- T6: Lookup de usuário inexistente (espera 404) ---' 'STEP'
$fakeEmail = 'nao-existe-' + [guid]::NewGuid().ToString('N').Substring(0,8) + '@bradesco.com.br'
$u404 = Get-UserByEmail -Email $fakeEmail
if ($u404.StatusCode -eq 404) {
    Add-Result 'T6' 'Lookup retorna 404 controlado' 'OK' "email=$fakeEmail"
} else {
    Add-Result 'T6' '404 controlado' 'FAIL' "Esperado 404, veio $($u404.StatusCode)"
}

# ============================================================
# T7 - Adicionar usuário em time INEXISTENTE: FAIL controlado
# ============================================================
Write-Log '--- T7: Adicionar usuário em time inexistente ---' 'STEP'
$ghost = 'time-fantasma-' + [guid]::NewGuid().ToString('N').Substring(0,6)
$lookGhost = Get-TeamExact -Name $ghost
if (-not $lookGhost.Found) {
    Add-Result 'T7' 'Time inexistente detectado antes do POST' 'OK' "name=$ghost (fluxo abortaria corretamente)"
} else {
    Add-Result 'T7' 'Time inexistente' 'FAIL' 'GUID aleatório bateu em time real?!'
}

# ============================================================
# T8 - Adicionar usuário NOVO ao time de teste
# ============================================================
Write-Log "--- T8: Adicionar '$($Config.User1)' ao time '$($Config.TeamNovo)' ---" 'STEP'
if (-not $script:TeamNovoId -or -not $script:User1Id) {
    Add-Result 'T8' 'Adicionar usuário' 'SKIP' 'Faltam pré-requisitos (TeamNovoId ou User1Id)'
} else {
    $members = Get-TeamMembers -TeamId $script:TeamNovoId
    $alreadyMember = $false
    if ($members.StatusCode -eq 200 -and $members.Body) {
        $alreadyMember = @($members.Body) | Where-Object { [int]$_.userId -eq $script:User1Id }
    }
    if ($alreadyMember) {
        Add-Result 'T8' "User1 já era membro" 'SKIP' 'Provavelmente run anterior'
    } elseif ($Config.DryRun) {
        Add-Result 'T8' 'Adicionar User1' 'SKIP' 'DryRun=true - POST não executado'
    } else {
        $add = Add-TeamMember -TeamId $script:TeamNovoId -UserId $script:User1Id
        if ($add.StatusCode -eq 200) {
            Add-Result 'T8' 'User1 adicionado' 'OK' "teamId=$($script:TeamNovoId) userId=$($script:User1Id)"
        } elseif ($add.StatusCode -eq 400 -and "$($add.RawError)" -match 'already added') {
            Add-Result 'T8' 'User1 já era membro (400 detectado)' 'SKIP' ''
        } else {
            Add-Result 'T8' 'Adicionar User1' 'FAIL' "HTTP $($add.StatusCode) - $($add.RawError)"
        }
    }
}

# ============================================================
# T9 - Repetir adição: deve dar SKIP (já é membro)
# ============================================================
Write-Log '--- T9: Repetir adição - deve dar SKIP ---' 'STEP'
if (-not $script:TeamNovoId -or -not $script:User1Id) {
    Add-Result 'T9' 'Re-adição idempotente' 'SKIP' 'Sem teamId/userId'
} else {
    $members2 = Get-TeamMembers -TeamId $script:TeamNovoId
    $isMember = $false
    if ($members2.StatusCode -eq 200 -and $members2.Body) {
        $isMember = @($members2.Body) | Where-Object { [int]$_.userId -eq $script:User1Id }
    }
    if ($isMember) {
        Add-Result 'T9' 'Lookup de membros confirma presença antes do POST' 'OK' 'POST seria evitado'
    } else {
        if ($Config.DryRun) {
            Add-Result 'T9' 'Re-adição' 'SKIP' 'DryRun=true, User1 não foi adicionado em T8'
        } else {
            Add-Result 'T9' 'Re-adição idempotente' 'FAIL' 'User1 não aparece na lista de membros'
        }
    }
}

# ============================================================
# T10 - Token inválido: 401 esperado
# ============================================================
Write-Log '--- T10: Token inválido deve retornar 401 ---' 'STEP'
$bad = Invoke-Grafana -Method GET -Path '/api/org' -OverrideToken 'glsa_invalid_token_for_test'
if ($bad.StatusCode -eq 401) {
    Add-Result 'T10' '401 com token inválido' 'OK' ''
} else {
    Add-Result 'T10' '401 com token inválido' 'FAIL' "Esperado 401, veio $($bad.StatusCode)"
}

# ============================================================
# T11 - Cleanup do time de teste
# ============================================================
Write-Log '--- T11: Cleanup ---' 'STEP'
if ($Config.DoCleanup -and -not $Config.DryRun -and $script:TeamNovoId) {
    $del = Remove-Team -TeamId $script:TeamNovoId
    if ($del.StatusCode -eq 200) {
        Add-Result 'T11' "Time '$($Config.TeamNovo)' removido" 'OK' "id=$($script:TeamNovoId)"
    } else {
        Add-Result 'T11' 'Cleanup' 'FAIL' "HTTP $($del.StatusCode) - $($del.RawError)"
    }
} else {
    Add-Result 'T11' 'Cleanup' 'SKIP' 'DoCleanup=false ou DryRun=true'
}

# ============================================================
# Relatório final
# ============================================================
Write-Host ''
Write-Host '============================================================' -ForegroundColor Cyan
Write-Host ' Relatório final' -ForegroundColor Cyan
Write-Host '============================================================' -ForegroundColor Cyan
$Results | Format-Table -AutoSize Test, Status, Description, Detail | Out-String | Write-Host

$summary = $Results | Group-Object Status | Sort-Object Name
Write-Host 'Resumo:' -ForegroundColor Cyan
$summary | ForEach-Object { Write-Host (" {0,-5}: {1}" -f $_.Name, $_.Count) }
Write-Host ''
Write-Host "Log completo: $LogFile" -ForegroundColor DarkGray

# Salva também um JSON estruturado pra anexar no RITM de evidência
$jsonPath = $LogFile -replace '\.log$', '.json'
$Results | ConvertTo-Json -Depth 5 | Set-Content -Path $jsonPath
Write-Host "JSON evidência: $jsonPath" -ForegroundColor DarkGray
