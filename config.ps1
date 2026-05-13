# ===========================================================
# Configuração dos testes - EDITE AQUI antes de rodar
# ===========================================================
# IMPORTANTE: este arquivo contém segredos. Não comite em Git.
# Após os testes, REVOGUE o token no Grafana e gere outro.
# ===========================================================

$Config = @{
    # URL base SEM barra no final
    BaseUrl  = 'https://grafana.prebanco.com.br'
    Token    = 'glsa_P4D0qk2qKqRhowjIAC7YtZWm7lqpCeet_1b80681c'
    OrgId    = 1

    # Times de teste
    TeamExistente = 'Observabilidade'    # esperado: já existe -> SKIP
    TeamNovo      = 'Teste'              # esperado: não existe -> CREATE

    # Usuários (e-mails sincronizados com AD, já logaram via SSO)
    User1 = 'adalberto.f.silva@bradesco.com.br'
    User2 = 'ademir.r.toledo@bradesco.com.br'

    # Comportamento
    DryRun      = $true   # $true = só GETs (não cria/adiciona nada). Comece com TRUE.
    DoCleanup   = $false  # $true = remove o time 'Teste' ao final (só ativa quando DryRun = $false)
    MaxRetries  = 3
    TimeoutSec  = 30
}
