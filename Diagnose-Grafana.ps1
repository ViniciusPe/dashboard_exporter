<#
.SYNOPSIS
    Diagnóstico rápido: confirma o nome exato do time, lista de usuários,
    permissões da service account e endpoints alternativos.

.DESCRIPTION
    Rode ANTES de Test-GrafanaIntegration.ps1 quando os testes falharem,
    para entender o que o Grafana realmente devolve.

    Não modifica nada (apenas GETs).
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $ScriptDir 'config.ps1')

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

$headers = @{
    'Authorization' = "Bearer $($Config.Token)"
    'Accept'        = 'application/json'
}
$base = $Config.BaseUrl.TrimEnd('/')

function Show-Section($title) {
    Write-Host ''
    Write-Host "===== $title =====" -ForegroundColor Cyan
}

function Try-Get($path) {
    try {
        $r = Invoke-WebRequest -Uri "$base$path" -Headers $headers -UseBasicParsing -TimeoutSec 30
        return @{ Status = [int]$r.StatusCode; Body = ($r.Content | ConvertFrom-Json -ErrorAction SilentlyContinue) ; Raw = $r.Content }
    } catch {
        $st = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        $body = $_.Exception.Message
        try {
            $sr = New-Object IO.StreamReader($_.Exception.Response.GetResponseStream())
            $body = $sr.ReadToEnd(); $sr.Close()
        } catch {}
        return @{ Status = $st; Body = $null; Raw = $body }
    }
}

# 1. Identidade
Show-Section '1. Quem sou eu? (GET /api/user)'
$me = Try-Get '/api/user'
Write-Host "HTTP $($me.Status)"
$me.Body | Format-List login, email, name, orgId, isGrafanaAdmin

# 2. Permissões na org
Show-Section '2. Org atual (GET /api/org)'
$org = Try-Get '/api/org'
Write-Host "HTTP $($org.Status)"
$org.Body | Format-List id, name

# 3. Todos os teams (paginado) — pra ver o NOME EXATO de Observabilidade
Show-Section "3. Procurar 'Observabilidade' com 3 estratégias"
Write-Host "3a) name=Observabilidade (match exato server-side)" -ForegroundColor DarkCyan
$a = Try-Get "/api/teams/search?name=$([uri]::EscapeDataString('Observabilidade'))"
Write-Host "HTTP $($a.Status) totalCount=$($a.Body.totalCount)"
$a.Body.teams | Format-Table id, name, memberCount

Write-Host "3b) query=Observabilidade (LIKE)" -ForegroundColor DarkCyan
$b = Try-Get "/api/teams/search?query=$([uri]::EscapeDataString('Observabilidade'))"
Write-Host "HTTP $($b.Status) totalCount=$($b.Body.totalCount)"
$b.Body.teams | Format-Table id, name, memberCount

Write-Host "3c) listar TODOS os teams (primeira página) e mostrar nomes" -ForegroundColor DarkCyan
$c = Try-Get "/api/teams/search?perpage=1000&page=1"
Write-Host "HTTP $($c.Status) totalCount=$($c.Body.totalCount)"
$c.Body.teams | Where-Object { $_.name -match 'obs' } | Format-Table id, name, memberCount
Write-Host "(filtrei nomes contendo 'obs' - confira o casing/espacos exatos acima)"

# 4. Lookup de usuário — 2 endpoints
Show-Section "4. Lookup de usuário (2 estratégias)"
$email = $Config.User1
Write-Host "4a) /api/users/lookup?loginOrEmail=$email (precisa Server Admin)" -ForegroundColor DarkCyan
$ua = Try-Get "/api/users/lookup?loginOrEmail=$([uri]::EscapeDataString($email))"
Write-Host "HTTP $($ua.Status) - $(if($ua.Status -eq 403){'CONFIRMADO: service account NAO eh server admin'}else{''})"
if ($ua.Body) { $ua.Body | Format-List id, email, login, name }

Write-Host "4b) /api/org/users?query=$email (basta Org Admin)" -ForegroundColor DarkCyan
$ub = Try-Get "/api/org/users?query=$([uri]::EscapeDataString($email))"
Write-Host "HTTP $($ub.Status) totalEncontrado=$(@($ub.Body).Count)"
@($ub.Body) | Format-Table userId, email, login, role

# 5. Anonymous?
Show-Section "5. Acesso anônimo? (GET /api/org SEM Authorization)"
try {
    $r = Invoke-WebRequest -Uri "$base/api/org" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    Write-Host "HTTP $($r.StatusCode) - ATENCAO: acesso anonimo HABILITADO em /api/org" -ForegroundColor Yellow
} catch {
    $st = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
    Write-Host "HTTP $st - OK (sem anonimo)" -ForegroundColor Green
}

Write-Host ''
Write-Host '===== Conclusões =====' -ForegroundColor Cyan
Write-Host '- Se 3a deu totalCount=0 e 3b/3c mostraram o time, o nome cadastrado tem diferença de casing/espaço.'
Write-Host '- Se 4a deu 403 e 4b deu 200, use /api/org/users no fluxo do ServiceNow.'
Write-Host '- Se 5 deu 200, /api/org permite anonimo - use /api/user para testar auth.'
