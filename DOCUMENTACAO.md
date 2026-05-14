# Grafana Alert Guard — Restrição de criação de alertas para usuários Editor

> Documento técnico da solução implantada no namespace `grafana-testing` (ARO) para impedir que usuários com role **Editor** criem ou modifiquem regras de alerta no Grafana OSS.

---

## 1. Contexto e problema

Estamos rodando **Grafana OSS 12.x** no ARO. A necessidade do time é:

> *Usuários com role **Editor** não devem conseguir criar nem modificar regras de alerta, contact points, notification policies, mute timings, templates e silences. Apenas usuários com role **Admin** (de org ou Grafana Admin global) podem fazer essas operações.*

### Por que não dá para resolver nativamente no Grafana OSS

A funcionalidade que permite editar as permissões dos papéis básicos (Viewer/Editor/Admin) chama-se **RBAC (Role-Based Access Control)** e está **disponível apenas no Grafana Enterprise e Grafana Cloud**, conforme a documentação oficial: <https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/access-control/>.

No Grafana OSS:

- Os papéis básicos são fixos e imutáveis.
- Editor sempre tem `alert.rules:create`, `alert.rules:write`, `alert.rules:delete`.
- A API `/api/access-control/roles/...` retorna 404.
- Folder permissions controlam apenas dashboards, **não** alert rules.

### Alternativas avaliadas

| Alternativa | Avaliação |
|---|---|
| Migrar para Grafana Enterprise | Solução oficial, mas requer licença |
| Rebaixar Editores para Viewer | Perde edição de dashboards — inaceitável |
| Fork e patch do código-fonte | Inviável de manter a cada upgrade |
| **Proxy reverso filtrando requests** | **Adotada** — sem custo de licença, sem alterar o Grafana |

---

## 2. Solução adotada — Alert Guard (proxy reverso)

Um proxy reverso (OpenResty/Nginx + Lua) é colocado **na frente do Service do Grafana**. Ele inspeciona o método HTTP e o path de cada request:

1. Se **não for** uma rota de escrita de alerta → repassa direto ao Grafana (zero overhead).
2. Se **for** uma rota de escrita de alerta:
   - Faz uma sub-request a `/api/user` e `/api/user/orgs` no Grafana, reaproveitando o cookie de sessão do usuário.
   - Se o usuário for **Grafana Admin global** OU **Admin na org atual** → libera.
   - Caso contrário → devolve **HTTP 403 Forbidden**.

### Rotas interceptadas

| Método | Path | Função |
|---|---|---|
| POST/PUT/DELETE/PATCH | `/api/ruler/grafana/*` | Criar/editar/excluir alert rules (UI usa esta API) |
| POST/PUT/DELETE/PATCH | `/api/v1/provisioning/alert-rules*` | Alert rules via API de provisioning |
| POST/PUT/DELETE/PATCH | `/api/v1/provisioning/contact-points*` | Contact points |
| POST/PUT/DELETE/PATCH | `/api/v1/provisioning/policies*` | Notification policies |
| POST/PUT/DELETE/PATCH | `/api/v1/provisioning/mute-timings*` | Mute timings |
| POST/PUT/DELETE/PATCH | `/api/v1/provisioning/templates*` | Notification templates |
| POST | `/api/alertmanager/grafana/config/api/v1/silences` | Criar silence |
| PUT/DELETE | `/api/alertmanager/grafana/config/api/v1/silence/*` | Editar/expirar silence |

### Diagrama (estado final em produção)

```
                      Mesma URL pública de sempre
                                |
                                v
   Usuário ──► Route (HAProxy do Router OpenShift, TLS edge :443)
                                |
                                v
                 Service: grafana-alert-guard (ClusterIP :8080)
                                |
                                v
                 Pod(s) OpenResty (alert-guard)
                                |
                ┌───────────────┴───────────────┐
                |                               |
        Request comum?                Request de escrita
        ──── repassa ────►            de alerta?
                                      ──► subrequest /api/user
                                          (com cookie do user)
                                      ──► subrequest /api/user/orgs
                                      Admin? sim ──► repassa
                                              não ──► HTTP 403
                                |
                                v
                  Service: grafana-a-service (ClusterIP :3000)
                                |
                                v
                          Pod(s) Grafana
```

### Por que o proxy é um Deployment **separado** (não sidecar)

| Critério | Vencedor |
|---|---|
| Não altera o Deployment do Grafana | **Separado** |
| Rollback simples (uma linha no Route) | **Separado** |
| Escala independente do Grafana | **Separado** |
| Atualizar o proxy não reinicia o Grafana | **Separado** |
| Performance | Empate (diferença irrelevante de ~0.3 ms) |

---

## 3. Validação em ambiente local (Docker)

Antes da implantação no ARO, a solução foi validada localmente com Docker Compose.

### 3.1. Estrutura

```
grafana-alert-guard/
├── docker-compose.yml
└── nginx/
    └── nginx.conf
```

### 3.2. `docker-compose.yml`

```yaml
services:
  grafana:
    image: grafana/grafana-oss:11.3.0
    container_name: grafana
    environment:
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: admin
      GF_AUTH_ANONYMOUS_ENABLED: "false"
      GF_SECURITY_COOKIE_SECURE: "false"
      GF_SERVER_ROOT_URL: "http://localhost:8080/"
    volumes:
      - grafana-data:/var/lib/grafana
    expose: ["3000"]    # NÃO publica 3000 — só acessamos via guard
    networks: [lab]
    restart: unless-stopped

  guard:
    image: openresty/openresty:alpine
    container_name: grafana-guard
    volumes:
      - ./nginx/nginx.conf:/usr/local/openresty/nginx/conf/nginx.conf:ro
    ports: ["8080:8080"]
    depends_on: [grafana]
    networks: [lab]
    restart: unless-stopped

networks:
  lab:
volumes:
  grafana-data:
```

### 3.3. Como subir

```powershell
cd grafana-alert-guard
docker compose up -d
```

Acesse `http://localhost:8080` (porta 3000 fica fechada propositalmente).

### 3.4. Roteiro de testes executado

1. Login como **admin** (`admin/admin`).
2. Criação de um usuário `editor1` com role **Editor**.
3. **Como admin**: criação de uma alert rule simples → ✅ sucesso.
4. **Como editor1**: tentativa de criação de alert rule → ❌ falha com **HTTP 403**.
5. **Como editor1**: criação/edição de dashboard → ✅ funciona normalmente.
6. **Como editor1**: queries no Explore → ✅ funciona.

### 3.5. Evidência do bloqueio nos logs

```
docker compose logs guard | findstr BLOCKED
```

Saída real do lab:
```
grafana-guard | 2026/05/14 13:21:48 [warn] 7#7: *1 [lua] content_by_lua(nginx.conf:58):32:
  authz: BLOCKED user='cu' role!=Admin method=POST
  uri=/api/ruler/grafana/api/v1/rules/dfm1ihulf0c1sb?subtype=cortex
  while sending to client, client: 172.24.0.1, server:, request:
  "POST /api/ruler/grafana/api/v1/rules/dfm1ihulf0c1sb?subtype=cortex HTTP/1.1",
  subrequest: "/_authz_admin", host: "localhost:8080",
  referrer: "http://localhost:8080/alerting/dfm1iibqcxiiof/edit"
```

### 3.6. Conclusão do lab

Solução validada. Editor recebe **403** ao tentar criar regras; Admin opera normalmente; recursos de uso comum (dashboards, queries, Explore) seguem intactos.

---

## 4. Implantação no ARO (`grafana-testing`)

### 4.1. Componentes criados

| Recurso | Nome | Função |
|---|---|---|
| ConfigMap | `grafana-alert-guard-config` | Contém o `nginx.conf` |
| Deployment | `grafana-alert-guard` | 2 réplicas OpenResty |
| Service | `grafana-alert-guard` | ClusterIP :8080 |
| Route (alterado) | `grafana-test-route` | Passa a apontar para o Service do guard |

> O Service `grafana-a-service` e o Deployment `grafana-a-deployment` do Grafana **não foram alterados**.

### 4.2. Imagem do guard

A imagem oficial `openresty/openresty:alpine` não respeita o SCC `restricted-v2` do OpenShift (que atribui UID aleatório). Foi construída uma imagem derivada com permissões ajustadas para o GID 0:

**Dockerfile:**
```dockerfile
FROM openresty/openresty:alpine

RUN set -eux; \
    mkdir -p /var/run/openresty /var/log/openresty /var/cache/nginx /tmp/nginx; \
    chgrp -R 0 /var/run/openresty /var/log/openresty /var/cache/nginx /tmp/nginx /usr/local/openresty; \
    chmod -R g=u /var/run/openresty /var/log/openresty /var/cache/nginx /tmp/nginx /usr/local/openresty

EXPOSE 8080
```

Publicada em: `quay.io/viniciuspe/openresty-guard:1.0` (pública).

### 4.3. Manifests

#### ConfigMap — `01-configmap.yaml`

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-alert-guard-config
  namespace: grafana-testing
data:
  nginx.conf: |
    pid /tmp/nginx/nginx.pid;
    worker_processes auto;
    error_log /dev/stderr info;
    events { worker_connections 2048; }

    http {
        access_log /dev/stdout;

        client_body_temp_path /tmp/nginx/client_body;
        proxy_temp_path       /tmp/nginx/proxy;
        fastcgi_temp_path     /tmp/nginx/fastcgi;
        uwsgi_temp_path       /tmp/nginx/uwsgi;
        scgi_temp_path        /tmp/nginx/scgi;

        # Rotas de ESCRITA de alerta que devem exigir Admin
        map "$request_method:$uri" $is_alert_write {
            default 0;
            "~*^(POST|PUT|DELETE|PATCH):/api/ruler/grafana/"                                                                   1;
            "~*^(POST|PUT|DELETE|PATCH):/api/v1/provisioning/(alert-rules|contact-points|policies|mute-timings|templates)"     1;
            "~*^POST:/api/alertmanager/grafana/config/api/v1/silences"                                                         1;
            "~*^(PUT|DELETE):/api/alertmanager/grafana/config/api/v1/silence/"                                                 1;
        }

        upstream grafana_upstream {
            server grafana-a-service.grafana-testing.svc.cluster.local:3000;
            keepalive 32;
        }

        server {
            listen 8080;
            client_max_body_size 50m;

            location = /_grafana_api_user {
                internal;
                proxy_pass http://grafana_upstream/api/user;
                proxy_pass_request_body off;
                proxy_set_header Content-Length "";
                proxy_set_header Host $host;
                proxy_set_header Cookie $http_cookie;
                proxy_set_header Authorization $http_authorization;
            }
            location = /_grafana_api_user_orgs {
                internal;
                proxy_pass http://grafana_upstream/api/user/orgs;
                proxy_pass_request_body off;
                proxy_set_header Content-Length "";
                proxy_set_header Host $host;
                proxy_set_header Cookie $http_cookie;
                proxy_set_header Authorization $http_authorization;
            }

            location = /_authz_admin {
                internal;
                content_by_lua_block {
                    local cjson = require "cjson.safe"

                    local res = ngx.location.capture("/_grafana_api_user")
                    if not res or res.status ~= 200 then
                        ngx.log(ngx.WARN, "authz: /api/user status=",
                                res and res.status or "nil")
                        return ngx.exit(403)
                    end

                    local user = cjson.decode(res.body) or {}

                    if user.isGrafanaAdmin == true then
                        return ngx.exit(200)
                    end

                    local current_org = user.orgId
                    if not current_org then return ngx.exit(403) end

                    local res2 = ngx.location.capture("/_grafana_api_user_orgs")
                    if not res2 or res2.status ~= 200 then return ngx.exit(403) end

                    local orgs = cjson.decode(res2.body) or {}
                    for _, o in ipairs(orgs) do
                        if o.orgId == current_org and o.role == "Admin" then
                            return ngx.exit(200)
                        end
                    end

                    ngx.log(ngx.WARN, "authz: BLOCKED user='", (user.login or "?"),
                            "' role!=Admin method=", ngx.var.request_method,
                            " uri=", ngx.var.request_uri)
                    return ngx.exit(403)
                }
            }

            location / {
                error_page 418 = @guarded;
                if ($is_alert_write = 1) { return 418; }

                proxy_http_version 1.1;
                proxy_set_header Host              $host;
                proxy_set_header X-Real-IP         $remote_addr;
                proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto $scheme;
                proxy_set_header Upgrade           $http_upgrade;
                proxy_set_header Connection        "upgrade";
                proxy_read_timeout 300s;

                proxy_pass http://grafana_upstream;
            }

            location @guarded {
                auth_request /_authz_admin;

                proxy_http_version 1.1;
                proxy_set_header Host              $host;
                proxy_set_header X-Real-IP         $remote_addr;
                proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto $scheme;
                proxy_pass http://grafana_upstream;
            }
        }
    }
```

#### Deployment — `02-deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: grafana-alert-guard
  namespace: grafana-testing
  labels:
    app: grafana-alert-guard
spec:
  replicas: 2
  selector:
    matchLabels:
      app: grafana-alert-guard
  strategy:
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
    type: RollingUpdate
  template:
    metadata:
      labels:
        app: grafana-alert-guard
    spec:
      securityContext:
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: openresty
          image: quay.io/viniciuspe/openresty-guard:1.0
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
              protocol: TCP
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            capabilities:
              drop: ["ALL"]
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 500m
              memory: 256Mi
          readinessProbe:
            tcpSocket: { port: 8080 }
            initialDelaySeconds: 3
            periodSeconds: 5
          livenessProbe:
            tcpSocket: { port: 8080 }
            initialDelaySeconds: 10
            periodSeconds: 15
          volumeMounts:
            - name: nginx-conf
              mountPath: /usr/local/openresty/nginx/conf/nginx.conf
              subPath: nginx.conf
              readOnly: true
            - name: tmp
              mountPath: /tmp/nginx
            - name: run
              mountPath: /var/run/openresty
      volumes:
        - name: nginx-conf
          configMap:
            name: grafana-alert-guard-config
            items:
              - key: nginx.conf
                path: nginx.conf
        - name: tmp
          emptyDir: {}
        - name: run
          emptyDir: {}
```

#### Service — `03-service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: grafana-alert-guard
  namespace: grafana-testing
  labels:
    app: grafana-alert-guard
spec:
  type: ClusterIP
  selector:
    app: grafana-alert-guard
  ports:
    - name: http
      port: 8080
      targetPort: http
      protocol: TCP
```

#### Route (alteração no existente) — `grafana-test-route`

Não foi criado um Route novo. O Route existente foi editado, alterando **apenas** os campos abaixo:

```yaml
spec:
  to:
    kind: Service
    name: grafana-alert-guard   # antes: grafana-a-service
  port:
    targetPort: http             # antes: grafana
```

Demais campos (host, path, TLS, etc.) permanecem inalterados. A URL pública não muda:

```
https://grafana-test-route-grafana-testing.apps.arodvplattedi11.arocorpp.bradesco.com.br
```

### 4.4. Passo a passo de implantação (Console OpenShift)

> Pré-condição: imagem `quay.io/viniciuspe/openresty-guard:1.0` publicada e pública.

1. **Projeto**: confirmar no canto superior do console que está em `grafana-testing`.
2. **Criar ConfigMap**: `+` Import YAML → colar conteúdo de `01-configmap.yaml` → Create.
3. **Criar Deployment**: `+` Import YAML → colar `02-deployment.yaml` → Create.
   - Aguardar 2 pods `Running` em **Workloads → Deployments → grafana-alert-guard**.
4. **Criar Service**: `+` Import YAML → colar `03-service.yaml` → Create.
5. **Apontar o Route**: **Networking → Routes → grafana-test-route → aba YAML** → alterar somente `spec.to.name` e `spec.port.targetPort` conforme acima → Save.

### 4.5. Validação no ARO

| Cenário | Resultado esperado | Resultado obtido |
|---|---|---|
| Admin cria alert rule | ✅ sucesso | ✅ |
| Editor cria alert rule | ❌ 403 Forbidden | ✅ confirmado |
| Editor cria dashboard | ✅ sucesso | ✅ |
| Editor edita dashboard | ✅ sucesso | ✅ |
| Editor executa query no Explore | ✅ sucesso | ✅ |

Mensagem do navegador quando Editor tenta criar regra:
```
<html><head><title>403 Forbidden</title></head>
<body><center><h1>403 Forbidden</h1></center>
<hr><center>openresty/1.29.2.3</center></body></html>
```

### 4.6. Rollback

Se for necessário reverter, editar `grafana-test-route` no console e voltar a apontar para o Service original:

```yaml
spec:
  to:
    kind: Service
    name: grafana-a-service
  port:
    targetPort: grafana
```

Reversão instantânea, sem necessidade de remover o Deployment/Service do guard.

---

## 5. Operação

### 5.1. Logs do guard

**Workloads → Deployments → grafana-alert-guard → Pods → (qualquer pod) → Logs**.

Filtros úteis no log (botão **Raw** + Ctrl+F):

| Procurar | Significado |
|---|---|
| `BLOCKED` | Bloqueio efetivado para usuário não-Admin |
| `authz: /api/user status=` | Subrequest de autorização falhou (token expirado, sessão inválida) |
| `POST /api/ruler` | Tentativas de criar/editar alert rules |
| `silences` | Operações em silences |

### 5.2. Atualização da configuração

1. Editar o ConfigMap `grafana-alert-guard-config` no console.
2. **Workloads → Deployments → grafana-alert-guard → Actions → Restart rollout** (os pods relêem o ConfigMap ao reiniciar).

### 5.3. Atualização da imagem do guard

1. Build local + push para Quay com nova tag (ex.: `:1.1`).
2. Editar o Deployment `grafana-alert-guard` no console e atualizar a tag em `spec.template.spec.containers[0].image`.
3. OpenShift faz rolling update automaticamente (sem downtime, pois `replicas: 2` e `maxUnavailable: 0`).

### 5.4. Comportamento esperado em HA do Grafana

A solução é compatível com múltiplas réplicas do Grafana, desde que:

- PVC do banco do Grafana seja **RWX** (já é o caso do ambiente).
- Cookie de sessão funcione consistentemente entre as réplicas (atendido com o banco compartilhado).
- Se for habilitar HA do alerting interno (porta 9094), configurar `ha_peers` no `grafana.ini`. Sem isso, regras podem disparar notificações duplicadas — isso é uma preocupação do **Grafana**, não do guard.

---

## 6. Limitações conhecidas

1. **A UI ainda mostra os botões “New alert rule”, “New silence” etc. para Editor.** O clique falha com 403, mas visualmente o botão está lá. Para esconder os botões só com RBAC (Enterprise/Cloud).
2. **A imagem do guard precisa ser mantida pela equipe** caso a base `openresty/openresty:alpine` receba CVEs. Recomenda-se rebuild periódico.
3. **Atualizações do Grafana podem mudar paths da API.** Em cada upgrade, validar com DevTools (F12 → Network) se os endpoints listados na seção 2 ainda são os corretos. Atualizar o `map` no ConfigMap se necessário.
4. **Bypass interno**: qualquer pod do cluster pode chamar `grafana-a-service:3000` diretamente, contornando o guard. Para fechar isso, aplicar uma `NetworkPolicy` permitindo tráfego ao Service do Grafana apenas dos pods do guard. **Não implementado** neste ambiente de testes.
5. **Provisionamento via arquivo** (`/etc/grafana/provisioning/alerting/*.yaml`) não passa pela API HTTP e portanto **não é bloqueado** pelo guard — o que é o comportamento desejado (GitOps continua funcionando).

---

## 7. Referências

- Documentação oficial RBAC: <https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/access-control/>
- Configure RBAC para Alerting: <https://grafana.com/docs/grafana/latest/alerting/set-up/configure-rbac/>
- OpenResty: <https://openresty.org/>
- OpenShift SCCs: <https://docs.openshift.com/container-platform/latest/authentication/managing-security-context-constraints.html>

---

## 8. Anexo — Imagem usada

- Registry: `quay.io/viniciuspe/openresty-guard`
- Tag em produção: `1.0`
- Base: `openresty/openresty:alpine` (OpenResty 1.29.x)
- Repositório do Dockerfile/manifests: *(adicionar link interno do Git aqui)*
