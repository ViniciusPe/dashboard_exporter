# Teste de integração ServiceNow → Grafana

Pacote de teste local para validar o fluxo descrito em
`ServiceNow-Grafana-Integration.md` antes de implementar no ServiceNow.

## Arquivos

| Arquivo | Função |
|---------|--------|
| `config.ps1` | Variáveis de ambiente (URL, token, times, e-mails, flags) — **EDITAR ANTES DE RODAR** |
| `Test-GrafanaIntegration.ps1` | Script principal com 11 cenários de teste |
| `log/run-*.log` | Log textual de cada execução (gerado automaticamente) |
| `log/run-*.json` | Log estruturado para anexar como evidência |

## Como usar

### 1. Copiar para a máquina com acesso ao Grafana

Copie a pasta inteira `grafana-test/` para o desktop com acesso a
`https://grafana.prebanco.com.br`.

### 2. Permitir execução de scripts (1ª vez)

Abra o PowerShell **como usuário normal** (não precisa admin) e libere apenas
para a sessão atual:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### 3. Conferir o `config.ps1`

Abra `config.ps1` e confirme as variáveis. **Comece sempre com `DryRun = $true`**.

### 4. Primeira execução — DRY RUN (só leitura)

```powershell
cd C:\caminho\onde\copiou\grafana-test
.\Test-GrafanaIntegration.ps1
```

Esta execução só faz `GET` — não cria nem modifica nada. Valida:

- T1 — conectividade e token
- T2 — `Observabilidade` existe (esperado: SKIP/OK)
- T3 — `Teste` não existe (esperado: SKIP com mensagem "DryRun")
- T5 — lookup dos dois usuários traz `userId`
- T6 — lookup de e-mail inventado retorna 404 controlado
- T7 — lookup de time inventado retorna "não existe"
- T10 — token aleatório retorna 401

Se T1, T2, T5, T6, T7 e T10 derem **OK**, a infraestrutura está ok.

### 5. Segunda execução — REAL (com POST)

Edite `config.ps1`:

```powershell
DryRun    = $false   # agora vai criar o time e adicionar membro
DoCleanup = $false   # ainda sem apagar
```

Rode de novo. Agora deve criar o time `Teste` (T3 = OK) e adicionar
`adalberto.f.silva@bradesco.com.br` nele (T8 = OK).

### 6. Terceira execução — idempotência

Rode **outra vez** sem mudar nada. Agora os mesmos testes devem virar
`SKIP` (time já existia, usuário já era membro). Isso prova que o fluxo
é blindado contra re-execuções — fundamental para o ServiceNow.

### 7. Cleanup final

```powershell
DryRun    = $false
DoCleanup = $true
```

Rode mais uma vez. O time `Teste` será removido (T11 = OK), deixando o
ambiente limpo.

## Interpretação dos status

| Status | Significado |
|--------|-------------|
| `OK`   | Caminho feliz validado |
| `SKIP` | Esperado e correto (idempotência, DryRun, ou pré-requisito não atendido) |
| `FAIL` | Comportamento divergente do documento — investigar antes de implementar no SN |

## Próximos passos depois dos testes

1. ✅ Anexar os arquivos `log/run-*.json` como evidência da homologação.
2. ✅ **Revogar o token** usado no teste e gerar um novo, exclusivo, para a
   integração definitiva do ServiceNow.
3. ✅ Replicar a lógica do PowerShell em **Scripted REST / Flow Designer**
   no ServiceNow, usando os mesmos endpoints e tratamentos (o
   pseudocódigo da seção 9 do documento principal já mapeia 1-para-1).

## Segurança

- O `config.ps1` contém o Bearer Token. **Não comite em Git.**
- Adicione `config.ps1` e `log/` ao `.gitignore` se for versionar.
- Após o teste final, revogue o token no Grafana
  (*Administration → Service accounts → svc → Tokens → Revoke*).
