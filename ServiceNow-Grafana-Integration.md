# Integração ServiceNow → Grafana

> **Status:** ✅ Catálogos 1 e 2 homologados em 2026-05-13 contra Grafana 11.3.0 · Catálogos 3 e 4 em homologação
> **Owner:** Squad de Observabilidade — **Versão:** 1.4

---

## 1. O que precisa ser feito

Quatro catálogos no ServiceNow chamam a API do Grafana:

| Catálogo | Entrada | O que faz |
|----------|---------|-----------|
| **1 — Criar Time** | `team_name` (= assignment group do SN) | Cria um Team no Grafana se ainda não existir |
| **2 — Adicionar Usuário em Time** | `team_name` + `user_email` (AD-synced) | Adiciona o usuário ao time se ainda não for membro |
| **3 — Criar Pasta** | `folder_path` (autocomplete, separador `/`) | Garante a hierarquia de pastas (cria os níveis que faltarem) |
| **4 — Atribuir Time a Pasta** | `team_name` + `folder_path` (autocomplete) | Concede permissão **Editor** ao time na pasta final |

**Regra de ouro:** sempre `GET` antes de `POST`. Garante idempotência — re-executar o mesmo RITM não duplica nada.

---

## 2. Configuração obrigatória

**No Grafana**
- Service Account com role **`Admin`** (Org Admin é suficiente — não precisa Server Admin)
- Token sem expiração curta, gerado em *Administration → Service accounts → Add token*

**No ServiceNow**
- Token no **Credential Store** (criptografado at-rest)
- Tabela `x_grafana_team_map`: `assignment_group` ↔ `grafana_team_id`
- Tabela `x_grafana_folder_map`: `folder_path` ↔ `folder_uid` (auditoria das pastas geridas pelo fluxo)
- Tabela `x_grafana_team_folder`: `team_id` ↔ `folder_uid` (auditoria das vinculações time–pasta)
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
| Buscar pasta (autocomplete) | `GET`  | `/api/search?type=dash-folder&query={txt}&limit=20` |
| Resolver ancestrais de uma pasta | `GET`  | `/api/folders/{uid}/parents` |
| Listar pastas-filhas de uma pasta | `GET`  | `/api/folders?parentUid={uid}&limit=1000` |
| Criar pasta | `POST` | `/api/folders` |
| Ler permissões da pasta | `GET`  | `/api/folders/{uid}/permissions` |
| Definir permissões da pasta | `POST` | `/api/folders/{uid}/permissions` |

> ⚠️ **Não usar** `/api/users/lookup` — exige Server Admin e retorna **403** com Service Account de Org.
> ⚠️ `POST /api/folders/{uid}/permissions` **substitui** todas as permissões — sempre fazer GET, fazer merge no array e depois POST.

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

## 6. Fluxo 2 — Adicionar Usuário em Time

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

## 7. Fluxo 3 — Criar Pasta

Cria a hierarquia de pastas indicada pelo `folder_path`. Funciona em 3 cenários, todos pelo mesmo loop:

| Caso | Situação | Comportamento |
|------|----------|---------------|
| **A** | Caminho 100% existente | SKIP em todos os níveis |
| **B** | Hierarquia parcial existe, falta a última pasta | Cria só a pasta final |
| **C** | Caminho 100% novo (1ª squad de uma tribo) | Cria todos os níveis em cascata |

### Entrada

| Campo | Origem | Exemplo |
|-------|--------|---------|
| `folder_path` | Texto com autocomplete, separador `/` | `Tribo Infra / Compute / Linux` |

### 7.1 Autocomplete no formulário (lookup AJAX)

O Service Portal/Workspace consulta enquanto o requester digita:

```http
GET /api/search?type=dash-folder&query={txt}&limit=20
Authorization: Bearer {{token}}
```

```json
[
  { "uid": "ccc333", "title": "Linux",       "folderUid": "bbb222", "folderTitle": "Compute" },
  { "uid": "eee555", "title": "Linux Tools", "folderUid": "fff666", "folderTitle": "Banking" }
]
```

Para montar o caminho completo na sugestão, chamar uma vez por resultado:

```http
GET /api/folders/ccc333/parents
```

```json
[
  { "uid": "aaa111", "title": "Tribo Infra" },
  { "uid": "bbb222", "title": "Compute" }
]
```

→ Exibir como `Tribo Infra / Compute / Linux`. O valor selecionado preenche o campo `folder_path`. O campo aceita **valor fora da lista** para os casos B e C.

### 7.2 Garantir a hierarquia (loop por segmento)

Quebrar `folder_path` em segmentos por `/`, normalizando cada um com `trim()`.
Para cada segmento, partindo de `parentUid = null` (raiz):

**a) Procurar pasta existente nesse nível:**

```http
GET /api/folders?parentUid={parentUid}&limit=1000
Authorization: Bearer {{token}}
```

> Para o nível raiz, omitir o parâmetro `parentUid`. Comparar `title.trim().toLowerCase()` com o segmento atual.

- **Encontrou** → `parentUid = uid_encontrado`, próximo segmento.
- **Não encontrou** → criar (passo b).

**b) Criar pasta no nível atual:**

```http
POST /api/folders
Authorization: Bearer {{token}}
Content-Type: application/json

{
  "title": "Storage",
  "parentUid": "bbb222"
}
```

```json
{ "uid": "ggg777", "id": 12, "title": "Storage", "parentUid": "bbb222" }
```

→ `parentUid = "ggg777"`, próximo segmento.

> Para criar pasta-raiz, omitir `parentUid` no body.

Ao final do loop, `parentUid` aponta para o **UID da pasta final** (alvo do `folder_path`). Persistir `folder_path` ↔ `folder_uid` em `x_grafana_folder_map`. RITM *Closed Complete*.

### 7.3 Confirmação no formulário (UX)

Antes do submit, mostrar ao requester um preview baseado no que o autocomplete sabe:

> *"Pasta-alvo: **Tribo Infra / Compute / Linux**. Pastas a serem criadas: nenhuma."*

Quando há criação parcial:

> *"Pastas a serem criadas: **Storage** (dentro de Compute)."*

Para caso C (raiz nova):

> *"⚠️ Esse caminho criará 3 pastas novas: Tribo Cyber, SOC, Threat Hunting. Confirmar?"*

### 7.4 Tratamento de erros

| Cenário | Ação |
|---------|------|
| `409` ao criar pasta (race) | Re-executar o GET do passo 7.2a para o nível e seguir |
| `401 / 403` | RITM *Work in Progress*, notificar owner |
| `429 / 5xx` | Retry backoff 3x. Persistindo → notificar owner |

---

## 8. Fluxo 4 — Atribuir Time a Pasta

Concede permissão **Editor** (`permission: 2`) ao time na pasta indicada.

### Entrada

| Campo | Origem | Exemplo |
|-------|--------|---------|
| `team_name` | Assignment group | `INFD-P - LINUX VM` |
| `folder_path` | Texto com autocomplete | `Tribo Infra / Compute / Linux` |

### 8.1 Resolver `teamId`

Buscar em `x_grafana_team_map` pelo `team_name`. Sem registro → executar 5.1 para confirmar via API. Se não existir → RITM *Closed Incomplete*:
> *"O time '{name}' não existe no Grafana. Abra antes o catálogo nº 1."*

### 8.2 Resolver `folderUid`

Usar o autocomplete (passo 7.1) — o requester seleciona uma pasta existente, e o SN guarda o `uid` retornado. Se o requester insistir em um caminho que **não existe**, RITM *Closed Incomplete*:
> *"A pasta '{path}' não existe no Grafana. Abra antes o catálogo nº 3."*

> 💡 *Otimização:* se `x_grafana_folder_map` já tiver `folder_path` ↔ `folder_uid`, pular o autocomplete e usar direto.

### 8.3 Ler permissões atuais

```http
GET /api/folders/abc123/permissions
Authorization: Bearer {{token}}
```

```json
[
  { "role": "Viewer", "permission": 1, "inherited": true },
  { "role": "Editor", "permission": 2, "inherited": true },
  { "teamId": 5, "permission": 2 }
]
```

### 8.4 Decidir

- Já há entrada com `teamId == teamIdAlvo` e `permission == 2` → **SKIP**, RITM *Closed Complete*.
- Senão, montar novo array preservando as permissões existentes + adicionando/atualizando a do time.

### 8.5 Gravar permissões

```http
POST /api/folders/abc123/permissions
Authorization: Bearer {{token}}
Content-Type: application/json

{
  "items": [
    { "role": "Viewer", "permission": 1 },
    { "role": "Editor", "permission": 2 },
    { "teamId": 5, "permission": 2 },
    { "teamId": 8, "permission": 2 }
  ]
}
```

```json
{ "message": "Folder permissions updated" }
```

Persistir em `x_grafana_team_folder` (`team_id` + `folder_uid`). RITM *Closed Complete*.

> ⚠️ Esse POST **substitui todas as permissões da pasta**. Sempre incluir as entradas pré-existentes no `items`, **exceto** as `inherited: true` (que não podem ir no payload — vêm automaticamente das pastas-pai).

Permission codes: `1=View`, `2=Edit`, `4=Admin`.

### 8.6 Tratamento de erros

| Cenário | Ação |
|---------|------|
| `400 "permission already exists"` | Tratar como SKIP |
| `404` (pasta deletada entre passos) | Voltar ao 8.2 (uma vez) |
| `401 / 403` | RITM *Work in Progress*, notificar owner |
| `429 / 5xx` | Retry backoff 3x. Persistindo → notificar owner |

---

## 9. State machine do RITM

| Cenário | RITM | Notificar requester | Notificar owner |
|---------|------|---------------------|-----------------|
| Já existia (time / usuário / pasta / permissão) | Closed Complete | ✅ informativo | — |
| Criado / adicionado / atribuído | Closed Complete | ✅ | — |
| Time não existe (cat. 2 ou 4) | Closed Incomplete | ✅ orientar cat. 1 | — |
| Pasta não existe (cat. 4) | Closed Incomplete | ✅ orientar cat. 3 | — |
| Usuário sem 1º login SSO (cat. 2) | Closed Incomplete | ✅ orientar SSO | — |
| `401 / 403` | Work in Progress | ✅ | ✅ |
| `5xx` após 3 retries | Work in Progress | ✅ | ✅ |

> Este fluxo **não abre incidentes (INC) automáticos**.

---

## 10. Fluxograma

```mermaid
flowchart TD
    Start([RITM aberto]) --> TipoReq{Catálogo?}

    %% Cat 1
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

    %% Cat 2
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

    %% Cat 3
    TipoReq -->|3. Criar Pasta| P1[Quebrar folder_path por /]
    P1 --> P2[Loop por segmento<br/>parentUid inicial = null]
    P2 --> P3{Segmento existe<br/>neste nível?}
    P3 -->|Sim| P4[parentUid = uid existente]
    P3 -->|Não| P5[POST /api/folders<br/>title, parentUid]
    P5 --> P4
    P4 --> P6{Próximo segmento?}
    P6 -->|Sim| P3
    P6 -->|Não| P7[[SUCCESS — Closed Complete<br/>Salvar em x_grafana_folder_map]]

    %% Cat 4
    TipoReq -->|4. Atribuir Time a Pasta| A1[Resolver teamId<br/>via x_grafana_team_map]
    A1 --> A2{Time existe?}
    A2 -->|Não| A2N[[Closed Incomplete<br/>Orientar cat. 1]]
    A2 -->|Sim| A3[Resolver folderUid<br/>via autocomplete]
    A3 --> A4{Pasta existe?}
    A4 -->|Não| A4N[[Closed Incomplete<br/>Orientar cat. 3]]
    A4 -->|Sim| A5[GET /api/folders/uid/permissions]
    A5 --> A6{Time já é Editor?}
    A6 -->|Sim| A7[[SKIP — Closed Complete]]
    A6 -->|Não| A8[POST /api/folders/uid/permissions<br/>array completo + nova entrada]
    A8 --> A9{200?}
    A9 -->|Sim| A10[[SUCCESS — Closed Complete<br/>Salvar em x_grafana_team_folder]]
    A9 -->|401/403| AAuth[[Work in Progress<br/>Notificar owner]]
    A9 -->|5xx/429| ARetry{Retry<3?}
    ARetry -->|Sim| A8
    ARetry -->|Não| AErr[[Work in Progress<br/>Notificar owner]]

    classDef ok fill:#1f7a1f,color:#fff;
    classDef warn fill:#b58900,color:#fff;
    classDef err fill:#a33,color:#fff;
    class C5,U8Y,A7 warn;
    class C9,U11,P7,A10 ok;
    class CErr,U4N,U6N,UErr,CAuth,UAuth,A2N,A4N,AAuth,AErr err;
```

---

## 11. Exemplo end-to-end com dados reais

**Cenário A — Adicionar usuário em time existente** (catálogo 2):

| Passo | Request | Resposta (resumo) | Decisão |
|-------|---------|-------------------|---------|
| 1 | `GET /api/teams/search?query=Observabilidade` | `[{ id:1, name:"Observabilidade " }]` ← *com espaço!* | Match normalizado → `teamId=1` |
| 2 | `GET /api/org/users?query=adalberto.f.silva@bradesco.com.br` | `[{ userId:407, email:"adalberto.f.silva@..." }]` | `userId=407` |
| 3 | `GET /api/teams/1/members` | `[…lista sem userId 407…]` | Não é membro |
| 4 | `POST /api/teams/1/members` body `{"userId":407}` | `200 "Member added to Team"` | **Closed Complete** |

**Cenário B — Criar a árvore para uma tribo nova** (catálogo 3, caso C):
`folder_path = Tribo Cyber / SOC / Threat Hunting`

| Passo | Request | Resposta | Decisão |
|-------|---------|----------|---------|
| 1 | `GET /api/folders?limit=1000` (raiz) | Sem `Tribo Cyber` | Criar |
| 2 | `POST /api/folders {title:"Tribo Cyber"}` | `{uid:"aaa"}` | parentUid=aaa |
| 3 | `GET /api/folders?parentUid=aaa` | Vazio | Criar |
| 4 | `POST /api/folders {title:"SOC", parentUid:"aaa"}` | `{uid:"bbb"}` | parentUid=bbb |
| 5 | `GET /api/folders?parentUid=bbb` | Vazio | Criar |
| 6 | `POST /api/folders {title:"Threat Hunting", parentUid:"bbb"}` | `{uid:"ccc"}` | **Closed Complete**, folder_uid=ccc |

**Cenário C — Atribuir time à pasta criada acima** (catálogo 4):

| Passo | Request | Resposta | Decisão |
|-------|---------|----------|---------|
| 1 | (lookup `x_grafana_team_map`) | `teamId=12` | OK |
| 2 | (lookup `x_grafana_folder_map`) | `folder_uid=ccc` | OK |
| 3 | `GET /api/folders/ccc/permissions` | `[Viewer/1, Editor/2]` (herdadas) | Time ausente |
| 4 | `POST /api/folders/ccc/permissions` body com array + `{teamId:12, permission:2}` | `200` | **Closed Complete** |

Re-executando qualquer um desses 3 RITMs, todos os passos viram SKIP. **Zero alteração.**

---

## 12. Checklist de testes (UAT)

| # | Catálogo | Cenário | Esperado |
|---|----------|---------|----------|
| 1 | 1 | Criar time inexistente | Time criado, Closed Complete |
| 2 | 1 | Criar time existente | SKIP, Closed Complete |
| 3 | 2 | Add usuário em time inexistente | Closed Incomplete (orientar cat. 1) |
| 4 | 2 | Add usuário sem 1º login SSO | Closed Incomplete (orientar SSO) |
| 5 | 2 | Add usuário já-membro | SKIP, Closed Complete |
| 6 | 2 | Add usuário novo | Closed Complete |
| 7 | 3 | Criar pasta-raiz nova | Pasta criada, Closed Complete |
| 8 | 3 | Caminho 100% novo (3 níveis) | 3 pastas criadas, Closed Complete |
| 9 | 3 | Caminho 100% existente | SKIP em todos os níveis, Closed Complete |
| 10 | 3 | Só a última pasta nova | Cria só a folha, Closed Complete |
| 11 | 4 | Atribuir time a pasta existente (1ª vez) | Editor concedido, Closed Complete |
| 12 | 4 | Atribuir time que já era Editor | SKIP, Closed Complete |
| 13 | 4 | Atribuir com time inexistente | Closed Incomplete (orientar cat. 1) |
| 14 | 4 | Atribuir com pasta inexistente | Closed Incomplete (orientar cat. 3) |
| 15 | 1-4 | Re-executar mesmo RITM | Tudo SKIP — prova idempotência |
| 16 | 1-4 | Token inválido | Work in Progress + notificar owner |

---

## 13. Segurança

- Token no Credential Store; rotação anual.
- Logs em `x_grafana_audit` **não** devem conter o Bearer.
- HTTPS / TLS 1.2+ obrigatório.
- E-mail é PII — retenção conforme política (sugestão: 180 dias).

---

## 14. Changelog

| Versão | Data | Mudança |
|--------|------|---------|
| 1.0 | 2026-05-13 | Versão inicial |
| 1.1 | 2026-05-13 | Homologado contra Grafana 11.3.0 |
| 1.2 | 2026-05-13 | Documento enxugado, exemplos práticos com payloads reais |
| 1.3 | 2026-05-14 | Adicionado catálogo de pastas com autocomplete |
| 1.4 | 2026-05-15 | Catálogo de pastas separado em **3 — Criar Pasta** e **4 — Atribuir Time a Pasta**; revisão de terminologia (pasta final em vez de pasta-folha); pseudocódigo removido |
