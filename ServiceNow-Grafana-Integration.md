# Integração ServiceNow → Grafana

> **Status:** ✅ Homologado em 2026-05-13 contra Grafana 11.3.0
> **Owner:** Squad de Observabilidade — **Versão:** 1.2

---

## 1. O que precisa ser feito

Dois catálogos no ServiceNow chamam a API do Grafana:

| Catálogo | Entrada | O que faz |
|----------|---------|-----------|
| **1 — Criar Time** | `team_name` (= assignment group do SN) | Cria um Team no Grafana se ainda não existir |
| **2 — Adicionar Usuário** | `team_name` + `user_email` (AD-synced) | Adiciona o usuário ao time se ainda não for membro |

**Regra de ouro:** sempre `GET` antes de `POST`. Garante idempotência — re-executar o mesmo RITM não duplica nada.

---

## 2. Configuração obrigatória

**No Grafana**
- Service Account com role **`Admin`** (Org Admin é suficiente — não precisa Server Admin)
- Token sem expiração curta, gerado em *Administration → Service accounts → Add token*

**No ServiceNow**
- Token no **Credential Store** (criptografado at-rest)
- Tabela `x_grafana_team_map`: `assignment_group` ↔ `grafana_team_id`
- Tabela `x_grafana_audit`: `ritm`, `endpoint`, `method`, `request`, `response`, `status_code`, `timestamp`
- Conexão HTTPS / TLS 1.2+, via MID Server se o Grafana não for público

---

## 3. Autenticação (única para todos os requests)

```http
Authorization: Bearer glsa_xxxxxxxxxxxxx_xxxxxxxx
Content-Type: application/json
Accept: application/json
```

> ⚠️ Não usar Basic Auth nem `/api/auth/keys` (depreciado).

---

## 4. Endpoints utilizados

| Quando | Método | Endpoint |
|--------|--------|----------|
| Buscar time | `GET`  | `/api/teams/search?query={name}` |
| Criar time | `POST` | `/api/teams` |
| Buscar usuário por e-mail | `GET`  | `/api/org/users?query={email}` |
| Listar membros do time | `GET`  | `/api/teams/{teamId}/members` |
| Adicionar membro | `POST` | `/api/teams/{teamId}/members` |

> ⚠️ **Não usar** `/api/users/lookup` — exige Server Admin e retorna **403** com Service Account de Org.

---

## 5. Fluxo 1 — Criar Time

### 5.1 Buscar o time

```http
GET /api/teams/search?query=DevOps-Pagamentos
Authorization: Bearer {{token}}
```

```json
{
  "totalCount": 1,
  "teams": [
    { "id": 7, "name": "DevOps-Pagamentos", "memberCount": 5 }
  ]
}
```

### 5.2 Decidir

Procurar item onde `name.trim().toLowerCase() == team_name.trim().toLowerCase()`.

- **Match** → SKIP, salvar `id` no mapping, RITM *Closed Complete*.
- **Sem match** → ir para 5.3.

> ⚠️ A API faz LIKE server-side. Sem `trim` + `lowercase`, dá falso-positivo (`"DevOps"` retornaria `"DevOps-A"` e `"DevOps-B"`) ou falso-negativo (espaço invisível no cadastro).

### 5.3 Criar

```http
POST /api/teams
Authorization: Bearer {{token}}
Content-Type: application/json

{
  "name": "DevOps-Pagamentos",
  "email": "",
  "orgId": 1
}
```

```json
{ "teamId": 8, "message": "Team created" }
```

Salvar `teamId` no mapping → RITM *Closed Complete*.

### 5.4 Tratamento de erros

| Status | Ação |
|--------|------|
| `409` | Race condition — voltar ao 5.1 (uma vez) |
| `401 / 403` | RITM *Work in Progress*, notificar owner |
| `429 / 5xx` | Retry backoff (1s → 2s → 4s, 3x). Persistindo → notificar owner |

---

## 6. Fluxo 2 — Adicionar Usuário

### 6.1 Confirmar time

Idêntico ao 5.1. Sem match → RITM *Closed Incomplete*:
> *"O time '{name}' não existe no Grafana. Abra antes o catálogo nº 1."*

### 6.2 Resolver e-mail → userId

```http
GET /api/org/users?query=fulano@empresa.com
Authorization: Bearer {{token}}
```

```json
[
  {
    "userId": 407,
    "email": "fulano@empresa.com",
    "login": "fulano@empresa.com",
    "name": "Fulano da Silva",
    "role": "Editor"
  }
]
```

- Match (`email` ou `login` case-insensitive) → guardar `userId`.
- Array vazio → usuário **nunca logou via SSO**. RITM *Closed Incomplete*:
  > *"{email} precisa realizar o 1º login no Grafana via SSO antes de ser adicionado."*

### 6.3 Já é membro?

```http
GET /api/teams/8/members
Authorization: Bearer {{token}}
```

```json
[
  { "teamId": 8, "userId": 407, "email": "fulano@empresa.com" }
]
```

Algum item com `userId == userIdAlvo` → SKIP, RITM *Closed Complete*.

### 6.4 Adicionar

```http
POST /api/teams/8/members
Authorization: Bearer {{token}}
Content-Type: application/json

{ "userId": 407 }
```

```json
{ "message": "Member added to Team" }
```

RITM *Closed Complete*.

### 6.5 Tratamento de erros

| Status | Ação |
|--------|------|
| `400 "User is already added"` | Tratar como SKIP |
| `404` | Time/usuário deletado entre passos — repetir do 6.1 (uma vez) |
| `401 / 403` | RITM *Work in Progress*, notificar owner |
| `429 / 5xx` | Retry backoff 3x. Persistindo → notificar owner |

---

## 7. State machine do RITM

| Cenário | RITM | Notificar requester | Notificar owner |
|---------|------|---------------------|-----------------|
| Time/usuário já existia | Closed Complete | ✅ informativo | — |
| Time criado / usuário adicionado | Closed Complete | ✅ | — |
| Time não existe (cat. 2) | Closed Incomplete | ✅ orientar cat. 1 | — |
| Usuário sem 1º login SSO | Closed Incomplete | ✅ orientar SSO | — |
| `401 / 403` | Work in Progress | ✅ | ✅ |
| `5xx` após 3 retries | Work in Progress | ✅ | ✅ |

> Este fluxo **não abre incidentes (INC) automáticos**.

---

## 8. Fluxograma

```mermaid
flowchart TD
    Start([RITM aberto]) --> TipoReq{Tipo?}

    TipoReq -->|1. Criar Time| C3[GET /api/teams/search?query=name]
    C3 --> C4{Match normalizado?}
    C4 -->|Sim| C5[[SKIP — Closed Complete]]
    C4 -->|Não| C6[POST /api/teams]
    C6 --> C7{200?}
    C7 -->|Sim| C9[[SUCCESS — Closed Complete]]
    C7 -->|409| C3
    C7 -->|401/403| CAuth[[Work in Progress<br/>Notificar owner]]
    C7 -->|5xx/429| CRetry{Retry<3?}
    CRetry -->|Sim| C6
    CRetry -->|Não| CErr[[Work in Progress<br/>Notificar owner]]

    TipoReq -->|2. Add Usuário| U3[GET /api/teams/search?query=name]
    U3 --> U4{Time existe?}
    U4 -->|Não| U4N[[Closed Incomplete<br/>Orientar cat. 1]]
    U4 -->|Sim| U5[GET /api/org/users?query=email]
    U5 --> U6{Match?}
    U6 -->|Não| U6N[[Closed Incomplete<br/>Pedir 1º login SSO]]
    U6 -->|Sim| U7[GET /api/teams/id/members]
    U7 --> U8{Já membro?}
    U8 -->|Sim| U8Y[[SKIP — Closed Complete]]
    U8 -->|Não| U9[POST /api/teams/id/members]
    U9 --> U10{200?}
    U10 -->|Sim| U11[[SUCCESS — Closed Complete]]
    U10 -->|400 already added| U8Y
    U10 -->|401/403| UAuth[[Work in Progress<br/>Notificar owner]]
    U10 -->|5xx/429| URetry{Retry<3?}
    URetry -->|Sim| U9
    URetry -->|Não| UErr[[Work in Progress<br/>Notificar owner]]

    classDef ok fill:#1f7a1f,color:#fff;
    classDef warn fill:#b58900,color:#fff;
    classDef err fill:#a33,color:#fff;
    class C5,U8Y warn;
    class C9,U11 ok;
    class CErr,U4N,U6N,UErr,CAuth,UAuth err;
```

---

## 9. Pseudocódigo (cola e adapta no Flow Designer)

```javascript
// === FLUXO 1: Criar Time ===
function createTeam(teamName, ritm) {
    const target = teamName.trim().toLowerCase();
    const found = grafanaGET('/api/teams/search?query=' + encodeURIComponent(teamName));
    const match = (found.teams || []).find(t => (t.name || '').trim().toLowerCase() === target);

    if (match) {
        saveMapping(teamName, match.id);
        return closeRITM(ritm, 'Closed Complete', `Time já existia (id=${match.id})`);
    }

    const created = grafanaPOST('/api/teams', { name: teamName, email: '', orgId: 1 });
    if (created.status === 200) {
        saveMapping(teamName, created.body.teamId);
        return closeRITM(ritm, 'Closed Complete', `Time criado (id=${created.body.teamId})`);
    }
    if (created.status === 409) return createTeam(teamName, ritm);
    return handleError(created, ritm);
}

// === FLUXO 2: Adicionar Usuário ===
function addUser(teamName, email, ritm) {
    const target = teamName.trim().toLowerCase();
    const teams = grafanaGET('/api/teams/search?query=' + encodeURIComponent(teamName));
    const team = (teams.teams || []).find(t => (t.name || '').trim().toLowerCase() === target);
    if (!team) return closeRITM(ritm, 'Closed Incomplete', 'Time não existe. Abra catálogo 1.');

    // IMPORTANTE: /api/org/users (NÃO /api/users/lookup, que dá 403)
    const users = grafanaGET('/api/org/users?query=' + encodeURIComponent(email));
    const e = email.toLowerCase();
    const user = (users.body || []).find(u =>
        (u.email || '').toLowerCase() === e || (u.login || '').toLowerCase() === e
    );
    if (!user) return closeRITM(ritm, 'Closed Incomplete', 'Usuário precisa logar via SSO antes.');

    const members = grafanaGET(`/api/teams/${team.id}/members`);
    if ((members.body || []).some(m => m.userId === user.userId))
        return closeRITM(ritm, 'Closed Complete', 'Usuário já era membro.');

    const added = grafanaPOST(`/api/teams/${team.id}/members`, { userId: user.userId });
    if (added.status === 200) return closeRITM(ritm, 'Closed Complete', 'Usuário adicionado.');
    if (added.status === 400 && /already added/i.test(added.body.message))
        return closeRITM(ritm, 'Closed Complete', 'Usuário já era membro.');
    return handleError(added, ritm);
}
```

---

## 10. Exemplo end-to-end com dados reais (homologação)

**Cenário:** adicionar `adalberto.f.silva@bradesco.com.br` ao time `Observabilidade`.

| Passo | Request | Resposta (resumo) | Decisão |
|-------|---------|-------------------|---------|
| 1 | `GET /api/teams/search?query=Observabilidade` | `[{ id:1, name:"Observabilidade " }]` ← *com espaço!* | Match normalizado → `teamId=1` |
| 2 | `GET /api/org/users?query=adalberto.f.silva@bradesco.com.br` | `[{ userId:407, email:"adalberto.f.silva@..." }]` | `userId=407` |
| 3 | `GET /api/teams/1/members` | `[…lista sem userId 407…]` | Não é membro |
| 4 | `POST /api/teams/1/members` body `{"userId":407}` | `200 "Member added to Team"` | **Closed Complete** |

Re-executando o mesmo RITM, o passo 3 já acharia `userId 407` e o passo 4 seria pulado (SKIP).

---

## 11. Checklist de testes (UAT)

| # | Cenário | Esperado |
|---|---------|----------|
| 1 | Criar time inexistente | Time criado, RITM Closed Complete |
| 2 | Criar time existente | SKIP, Closed Complete |
| 3 | Add usuário em time inexistente | Closed Incomplete (orientar cat. 1) |
| 4 | Add usuário sem 1º login SSO | Closed Incomplete (orientar SSO) |
| 5 | Add usuário já-membro | SKIP, Closed Complete |
| 6 | Add usuário novo | Closed Complete |
| 7 | Re-executar mesmo RITM | Tudo SKIP — prova idempotência |
| 8 | Token inválido | Work in Progress + notificar owner |

---

## 12. Segurança

- Token no Credential Store; rotação anual.
- Logs em `x_grafana_audit` **não** devem conter o Bearer.
- HTTPS / TLS 1.2+ obrigatório.
- E-mail é PII — retenção conforme política (sugestão: 180 dias).

---

## 13. Changelog

| Versão | Data | Mudança |
|--------|------|---------|
| 1.0 | 2026-05-13 | Versão inicial |
| 1.1 | 2026-05-13 | Homologado contra Grafana 11.3.0 |
| 1.2 | 2026-05-13 | Documento enxugado, exemplos práticos com payloads reais |
